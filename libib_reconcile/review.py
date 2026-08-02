# libib_reconcile/review.py
#
# Interactive checkbook-style reconciliation review: for each "gap" book
# (scraped, no automated Libib match) a human can search/pick a candidate
# Libib entry the automated matcher missed and confirm it's actually already
# owned. Two files per output directory:
#   - reconcile_{timestamp}_review_snapshot.json — write-once, computed
#     alongside a reconcile run's other reports. The match assignment itself
#     is cheap to recompute (reconciler.reconcile() is deterministic), but
#     gap-book enrichment is a real network cost — this snapshot captures it
#     once so the review page never re-hits the network.
#   - reconcile_review_decisions.json — mutable, durable human judgment,
#     deliberately NOT timestamped like the snapshot: a decision made against
#     a gap book should still apply if that book reappears in a later
#     snapshot (e.g. a re-scrape before a re-export). Written atomically
#     (temp file + os.replace) since it's rewritten repeatedly.
#
# The same snapshot also backs the mirror-image "orphan" review: for each
# Libib entry the scrapes never matched (status "libib_only" in
# candidate_pool), a human can flag it as a duplicate of another Libib
# entry or as no longer owned. Decisions live in a sibling file,
# reconcile_orphan_decisions.json, keyed by stable_libib_key rather than
# stable_gap_key since there's no gap book on this side of the review.
#
# Confirmed matches never write back to Libib directly — only ever affect
# this tool's own output (excluded from the reviewed gap CSV, listed in a
# tag-suggestions report / orphan-review report the user applies by hand).

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, TypedDict

from lib import EnrichmentResult, classify_identifier, enrich_book

from libib_reconcile.libib_reader import LibibEntry
from libib_reconcile.output import write_gap_csv, write_tag_suggestions_report
from libib_reconcile.reconciler import (
    ReconcileResult,
    ScrapedBook,
    ScrapedBookResult,
    author_overlap,
    title_score,
)

_EXCLUDED_TAGS = frozenset({"deleted", "removed"})

# Persistent, cross-session record of "skipped" decisions — deliberately NOT
# scoped to any one output directory, unlike reconcile_review_decisions.json.
# A skip means "this exact book (library loan, short story, whatever) should
# never be in Libib" — a fact about the book, not about one reconcile run —
# so it needs to survive a fresh output directory being used next time
# (e.g. a dated per-session folder), not just a fresh snapshot in the same
# one. Fixed app-config location, matching the (planned) Google Books
# credentials path convention (~/.config/libibtools/...), so it's found
# automatically without a new path the user has to remember to pass in.
_GLOBAL_SKIP_LIST_PATH = os.path.join(
    os.path.expanduser("~"), ".config", "libibtools", "reconcile_skips.json"
)

_DEFAULT_RANK_LIMIT = 25
_DEFAULT_RANK_FLOOR = 0.15
_DEFAULT_SEARCH_LIMIT = 50

_DECISIONS_FILENAME = "reconcile_review_decisions.json"
_ORPHAN_DECISIONS_FILENAME = "reconcile_orphan_decisions.json"


class GapBookDict(TypedDict):
    key: str
    provider: str
    title: str
    author: str
    isbn: Optional[str]
    cover_url: str
    enrichment: dict


class LibibCandidateDict(TypedDict):
    key: str
    title: str
    creators: str
    tags: list[str]
    providers: list[str]
    ean_isbn13: str
    upc_isbn10: str
    status: str


class ReviewSnapshot(TypedDict):
    generated_at: str
    source_paths: dict[str, Optional[str]]
    gap_books: list[GapBookDict]
    candidate_pool: list[LibibCandidateDict]


@dataclass
class Decision:
    status: str  # "confirmed_match" | "confirmed_new" | "skipped"
    libib_key: Optional[str]
    decided_at: str
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "libib_key": self.libib_key,
            "decided_at": self.decided_at,
            "note": self.note,
        }

    @staticmethod
    def from_dict(d: dict) -> "Decision":
        return Decision(
            status=d["status"],
            libib_key=d.get("libib_key"),
            decided_at=d.get("decided_at", ""),
            note=d.get("note"),
        )


def _normalize(s: str) -> str:
    return (s or "").strip().lower()


def _hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def stable_gap_key(provider: str, book: ScrapedBook) -> str:
    """Content hash over immutable identity fields — provider is included
    since the same title can legitimately be two independently-decidable
    gap books (e.g. Kindle "Dune" and Kobo "Dune" both missing need two
    separate tag additions, not one shared decision)."""
    title, author, isbn, _cover = book
    return _hash(provider, _normalize(title), _normalize(author), isbn or "")


def stable_libib_key(entry: LibibEntry) -> str:
    """Content hash deliberately excluding tags/providers/status — those are
    exactly what a confirmed decision causes the user to change by hand in
    Libib, so including them would silently orphan the decision the moment
    the re-tag happens."""
    return _hash(
        _normalize(entry.title),
        _normalize(entry.creators),
        entry.ean_isbn13,
        entry.upc_isbn10,
    )


def _enrichment_to_dict(e: EnrichmentResult) -> dict:
    return {
        "isbn13": e.isbn13,
        "isbn10": e.isbn10,
        "description": e.description,
        "publisher": e.publisher,
        "publish_date": e.publish_date,
        "length_of": e.length_of,
        "series_name": e.series_name,
        "series_position": e.series_position,
    }


def _enrichment_from_dict(d: dict) -> EnrichmentResult:
    return EnrichmentResult(
        isbn13=d.get("isbn13"),
        isbn10=d.get("isbn10"),
        description=d.get("description"),
        publisher=d.get("publisher"),
        publish_date=d.get("publish_date"),
        length_of=d.get("length_of"),
        series_name=d.get("series_name"),
        series_position=d.get("series_position"),
    )


def _snapshot_path(output_dir: str, timestamp: str) -> str:
    return os.path.join(output_dir, f"reconcile_{timestamp}_review_snapshot.json")


def _decisions_path(output_dir: str) -> str:
    return os.path.join(output_dir, _DECISIONS_FILENAME)


def write_review_snapshot(
    result: ReconcileResult,
    enriched_gap_books: list[tuple[ScrapedBookResult, EnrichmentResult]],
    source_paths: dict[str, Optional[str]],
    output_dir: str,
    timestamp: str,
) -> str:
    """Write the write-once review snapshot. `enriched_gap_books` is exactly
    core.run()'s own (ScrapedBookResult, EnrichmentResult) pairs for the gap
    books — already status == "missing_from_libib", no re-filtering needed.

    The candidate pool includes every Libib entry regardless of match
    status (only `deleted`/`removed`-tagged entries are excluded) — a real
    bug found live (2026-07-25): a Libib entry the automated matcher had
    already claimed (status "matched") used to be excluded entirely, so a
    *wrong* auto-match (or a book legitimately owned on two platforms, where
    one platform's copy already matched) could never be found or corrected
    by search — "the option isn't there" was a real, reachable dead end, not
    user error. Since this feature's entire premise is "the algorithm might
    have missed or mismatched something," restricting the candidate pool to
    only what the algorithm *didn't* already decide on was backwards.
    """
    gap_books: list[GapBookDict] = [
        {
            "key": stable_gap_key(r.provider, r.book),
            "provider": r.provider,
            "title": r.book[0],
            "author": r.book[1],
            "isbn": r.book[2],
            "cover_url": r.book[3],
            "enrichment": _enrichment_to_dict(enrichment),
        }
        for r, enrichment in enriched_gap_books
    ]

    candidate_pool: list[LibibCandidateDict] = [
        {
            "key": stable_libib_key(m.entry),
            "title": m.entry.title,
            "creators": m.entry.creators,
            "tags": sorted(m.entry.tags),
            "providers": sorted(m.entry.providers),
            "ean_isbn13": m.entry.ean_isbn13,
            "upc_isbn10": m.entry.upc_isbn10,
            "status": m.status,
        }
        for m in result.libib_results
        if not (m.entry.tags & _EXCLUDED_TAGS)
    ]

    snapshot: ReviewSnapshot = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_paths": source_paths,
        "gap_books": gap_books,
        "candidate_pool": candidate_pool,
    }

    os.makedirs(output_dir, exist_ok=True)
    path = _snapshot_path(output_dir, timestamp)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)

    _apply_global_skips_to_new_gaps(gap_books, output_dir)

    return path


def load_review_snapshot(path: str) -> ReviewSnapshot:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {
        "generated_at": raw.get("generated_at", ""),
        "source_paths": raw.get("source_paths", {}),
        "gap_books": raw.get("gap_books", []),
        "candidate_pool": raw.get("candidate_pool", []),
    }


def gap_has_enrichment(gap: GapBookDict) -> bool:
    """True if any metadata field was ever actually found for this gap
    book — a fresh EnrichmentResult() (--no-enrich, or a fetch that found
    nothing) leaves every field None/falsy. Used both to flag books worth a
    manual re-enrichment attempt in the review UI and by
    refresh_gap_enrichment() itself to decide whether a fresh value should
    replace an existing one."""
    e = gap["enrichment"]
    return bool(
        e.get("description")
        or e.get("publisher")
        or e.get("publish_date")
        or e.get("length_of")
        or e.get("series_name")
    )


_MANUAL_ENRICHMENT_FIELDS = (
    "description",
    "publisher",
    "publish_date",
    "length_of",
    "series_name",
    "series_position",
)


def _find_gap(snapshot: ReviewSnapshot, gap_key: str) -> GapBookDict:
    gap = next((g for g in snapshot["gap_books"] if g["key"] == gap_key), None)
    if gap is None:
        raise KeyError(f"No gap book with key {gap_key!r} in this snapshot")
    return gap


def _rewrite_snapshot_atomically(snapshot_path: str, snapshot: ReviewSnapshot) -> None:
    tmp_path = snapshot_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2)
    os.replace(tmp_path, snapshot_path)


def refresh_gap_enrichment(snapshot_path: str, gap_key: str) -> GapBookDict:
    """On-demand re-enrichment for one gap book, triggered by hand from the
    review page — for books that came out of the original reconcile run
    (possibly a --no-enrich one) with little or no metadata, or where
    enrich_book()'s Open Library/Google Books/AI/Wikidata chain simply found
    nothing the first time. A single real network call, not job-backed —
    one book takes a few seconds, nowhere near scrape-job territory.

    Merges the fresh result into the existing one field-by-field (a new
    value wins only if it's actually truthy) rather than overwriting
    wholesale, so a retry that finds *less* than before (a flaky provider,
    AI_PROVIDER unset this time, ...) can never regress already-found data.

    Mutates the write-once snapshot in place (atomically) — "write-once"
    was about not needing to redo the whole computation, not a promise
    that a single book's captured metadata can never be refreshed by hand.
    """
    with open(snapshot_path, encoding="utf-8") as f:
        snapshot: ReviewSnapshot = json.load(f)
    gap = _find_gap(snapshot, gap_key)

    isbn = gap["isbn"]
    upc_isbn10, ean_isbn13 = classify_identifier(isbn) if isbn else ("", "")
    fresh = enrich_book(
        gap["title"],
        gap["author"],
        ean_isbn13 or None,
        upc_isbn10 or None,
        gap["cover_url"],
    )
    fresh_dict = _enrichment_to_dict(fresh)

    gap["enrichment"] = {
        field: (fresh_dict.get(field) or gap["enrichment"].get(field))
        for field in fresh_dict
    }

    _rewrite_snapshot_atomically(snapshot_path, snapshot)
    return gap


def set_manual_enrichment(
    snapshot_path: str, gap_key: str, fields: dict
) -> GapBookDict:
    """Let a human type metadata in directly — the other half of "not just
    AI": refresh_gap_enrichment() only ever pulls from Open Library/Google
    Books/AI/Wikidata, but often the person doing the review already knows
    the right publisher, page count, or series position and there was
    simply no way to just say so.

    Unlike refresh_gap_enrichment()'s "truthy wins" merge (which protects
    against an uncertain automated retry regressing good data), this sets
    exactly what's given, including an intentionally blank value — a human
    clearing a wrong description on purpose is a real edit, not noise to
    guard against. Only keys in `fields` that are also in
    _MANUAL_ENRICHMENT_FIELDS are applied; anything else is ignored.
    """
    with open(snapshot_path, encoding="utf-8") as f:
        snapshot: ReviewSnapshot = json.load(f)
    gap = _find_gap(snapshot, gap_key)

    gap["enrichment"] = {
        **gap["enrichment"],
        **{k: v for k, v in fields.items() if k in _MANUAL_ENRICHMENT_FIELDS},
    }

    _rewrite_snapshot_atomically(snapshot_path, snapshot)
    return gap


def _load_decisions_file(path: str) -> dict[str, Decision]:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {key: Decision.from_dict(value) for key, value in raw.items()}


def _write_decisions_file(path: str, decisions: dict[str, Decision]) -> None:
    """Atomic write (temp file + os.replace) — shared by the gap-book and
    orphan decision stores alike, both of which are rewritten repeatedly
    across a review session, unlike the write-once snapshot."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({key: d.to_dict() for key, d in decisions.items()}, f, indent=2)
    os.replace(tmp_path, path)


def load_decisions(output_dir: str) -> dict[str, Decision]:
    return _load_decisions_file(_decisions_path(output_dir))


def _write_decisions(output_dir: str, decisions: dict[str, Decision]) -> None:
    _write_decisions_file(_decisions_path(output_dir), decisions)


def _load_global_skips() -> dict[str, str]:
    """gap_key -> skipped_at (iso timestamp). See _GLOBAL_SKIP_LIST_PATH."""
    if not os.path.exists(_GLOBAL_SKIP_LIST_PATH):
        return {}
    with open(_GLOBAL_SKIP_LIST_PATH, encoding="utf-8") as f:
        return json.load(f)


def _save_global_skips(skips: dict[str, str]) -> None:
    os.makedirs(os.path.dirname(_GLOBAL_SKIP_LIST_PATH), exist_ok=True)
    tmp_path = _GLOBAL_SKIP_LIST_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(skips, f, indent=2)
    os.replace(tmp_path, _GLOBAL_SKIP_LIST_PATH)


def _sync_global_skip(gap_key: str, status: str) -> None:
    """Keep the persistent, cross-session global skip list in step with this
    decision: a "skipped" status remembers this exact book (by its content-
    hash key) forever, regardless of output directory, so a future session's
    write_review_snapshot() can pre-label it the moment it reappears as a
    gap (see _apply_global_skips_to_new_gaps). Changing your mind — undoing
    the skip, or picking a different outcome entirely — removes it from the
    global list too, so it doesn't silently re-skip itself later."""
    global_skips = _load_global_skips()
    if status == "skipped":
        if gap_key in global_skips:
            return
        global_skips[gap_key] = datetime.now().isoformat(timespec="seconds")
    else:
        if gap_key not in global_skips:
            return
        del global_skips[gap_key]
    _save_global_skips(global_skips)


def save_decision(
    output_dir: str,
    gap_key: str,
    status: str,
    libib_key: Optional[str] = None,
    note: Optional[str] = None,
) -> dict[str, Decision]:
    """Upsert one decision. status="undecided" clears a prior decision (undo)
    rather than storing an "undecided" record — absence from this file
    already means "pending" everywhere else in this module. Returns the
    full updated decisions dict."""
    decisions = load_decisions(output_dir)

    if status == "undecided":
        decisions.pop(gap_key, None)
    else:
        decisions[gap_key] = Decision(
            status=status,
            libib_key=libib_key,
            decided_at=datetime.now().isoformat(timespec="seconds"),
            note=note,
        )

    _write_decisions(output_dir, decisions)
    _sync_global_skip(gap_key, status)

    return decisions


def _orphan_decisions_path(output_dir: str) -> str:
    return os.path.join(output_dir, _ORPHAN_DECISIONS_FILENAME)


def load_orphan_decisions(output_dir: str) -> dict[str, Decision]:
    return _load_decisions_file(_orphan_decisions_path(output_dir))


def save_orphan_decision(
    output_dir: str,
    orphan_key: str,
    status: str,  # "duplicate" | "needs_archive" | "keep" | "undecided"
    libib_key: Optional[str] = None,
    note: Optional[str] = None,
) -> dict[str, Decision]:
    """Upsert one orphan decision — same "undecided clears" convention as
    save_decision(), in a sibling file (reconcile_orphan_decisions.json)
    rather than the gap-book decisions file, since the two are keyed in
    different spaces (stable_gap_key vs stable_libib_key) and reviewed on
    separate pages. No global-skip-list sync: that concept ("this exact
    scraped book should never be in Libib") is specific to gap books and
    doesn't apply to an existing Libib entry."""
    decisions = load_orphan_decisions(output_dir)

    if status == "undecided":
        decisions.pop(orphan_key, None)
    else:
        decisions[orphan_key] = Decision(
            status=status,
            libib_key=libib_key,
            decided_at=datetime.now().isoformat(timespec="seconds"),
            note=note,
        )

    _write_decisions_file(_orphan_decisions_path(output_dir), decisions)
    return decisions


def _apply_global_skips_to_new_gaps(
    gap_books: list[GapBookDict], output_dir: str
) -> None:
    """Pre-label any gap book whose content-hash key is already in the
    persistent global skip list (set in a previous session, possibly
    against a different output directory) as "skipped" in THIS session's
    own decisions file — a library loan or short story already told to
    this tool to ignore doesn't need to be re-decided every time it's
    re-scraped and shows up as a gap again. Never overwrites a decision
    this output directory already has for that key."""
    global_skips = _load_global_skips()
    if not global_skips:
        return

    decisions = load_decisions(output_dir)
    changed = False
    now = datetime.now().isoformat(timespec="seconds")

    for gap in gap_books:
        key = gap["key"]
        if key in global_skips and key not in decisions:
            decisions[key] = Decision(status="skipped", libib_key=None, decided_at=now)
            changed = True

    if changed:
        _write_decisions(output_dir, decisions)


_SNAPSHOT_PREFIX = "reconcile_"
_SNAPSHOT_SUFFIX = "_review_snapshot.json"


class SnapshotSummary(TypedDict):
    path: str
    generated_at: str
    gap_count: int
    decided_count: int


def list_review_snapshots(output_dir: str) -> list[SnapshotSummary]:
    """Scan a directory for review snapshot files, newest first — the way
    back to an in-progress review for someone who closed the browser tab
    (or even restarted the server) without keeping the exact snapshot path.
    Both the snapshot and the decisions file it needs are already durable on
    disk by design; this is purely the missing discovery step, not new
    persistence. Skips anything that fails to parse (a half-written file,
    something else entirely) rather than letting one bad file break the
    whole listing.
    """
    if not os.path.isdir(output_dir):
        return []

    decisions = load_decisions(output_dir)
    summaries: list[SnapshotSummary] = []

    for name in os.listdir(output_dir):
        if not (name.startswith(_SNAPSHOT_PREFIX) and name.endswith(_SNAPSHOT_SUFFIX)):
            continue
        path = os.path.join(output_dir, name)
        try:
            snapshot = load_review_snapshot(path)
        except (OSError, ValueError):
            continue

        gap_keys = [g["key"] for g in snapshot["gap_books"]]
        decided = sum(1 for key in gap_keys if key in decisions)
        summaries.append(
            {
                "path": path,
                "generated_at": snapshot["generated_at"],
                "gap_count": len(gap_keys),
                "decided_count": decided,
            }
        )

    summaries.sort(key=lambda s: s["generated_at"], reverse=True)
    return summaries


def _claimed_libib_keys(
    decisions: dict[str, Decision], exclude_gap_key: str
) -> set[str]:
    """libib_keys already confirmed_match-linked to a DIFFERENT gap book —
    excluded from future candidate lists ("crossed off the register"), but
    not from the gap book they're actually linked to, so revisiting that
    gap book still shows (and lets you undo) its own confirmed match."""
    return {
        d.libib_key
        for gap_key, d in decisions.items()
        if gap_key != exclude_gap_key and d.status == "confirmed_match" and d.libib_key
    }


def rank_candidates(
    gap: GapBookDict,
    candidate_pool: list[LibibCandidateDict],
    decisions: dict[str, Decision],
    limit: int = _DEFAULT_RANK_LIMIT,
    score_floor: float = _DEFAULT_RANK_FLOOR,
) -> list[dict]:
    """Top-N candidates by title similarity, without the automated matcher's
    plausibility gate — that gate is exactly what this feature exists to
    bypass. Returns [{"candidate": ..., "score": ..., "author_overlap": ...}],
    sorted by score descending, author-overlap-True first on a score tie,
    then key for full determinism."""
    claimed = _claimed_libib_keys(decisions, gap["key"])
    scored = []
    for c in candidate_pool:
        if c["key"] in claimed:
            continue
        score = title_score(gap["title"], c["title"])
        if score < score_floor:
            continue
        overlap = bool(
            gap["author"]
            and c["creators"]
            and author_overlap(gap["author"], c["creators"])
        )
        scored.append((score, overlap, c))

    scored.sort(key=lambda t: (-t[0], not t[1], t[2]["key"]))
    return [
        {"candidate": c, "score": score, "author_overlap": overlap}
        for score, overlap, c in scored[:limit]
    ]


def search_candidates(
    query: str,
    candidate_pool: list[LibibCandidateDict],
    decisions: dict[str, Decision],
    gap_key: str,
    limit: int = _DEFAULT_SEARCH_LIMIT,
) -> tuple[list[LibibCandidateDict], int]:
    """Plain substring match over title+author across the WHOLE pool,
    bypassing ranking entirely — the guaranteed fallback for titles too
    dissimilar for any scoring function to rank highly (translated/abridged
    titles, omnibus editions, anything the top-N suggestions missed).
    Returns (results, total_match_count) so the caller can show a
    "N more — refine your search" hint when the cap is hit."""
    claimed = _claimed_libib_keys(decisions, gap_key)
    needle = _normalize(query)
    matches = [
        c
        for c in candidate_pool
        if c["key"] not in claimed
        and (needle in _normalize(c["title"]) or needle in _normalize(c["creators"]))
    ]
    return matches[:limit], len(matches)


def list_orphans(snapshot: ReviewSnapshot) -> list[LibibCandidateDict]:
    """Libib entries tagged kindle/chirp/nook/bn (etc.) that no scrape
    matched — the mirror of gap_books, and the same set write_orphan_report()
    puts in reconcile_{ts}_orphans.txt. Read straight off candidate_pool's
    own `status` field (set by reconciler.reconcile()) rather than a
    separate snapshot field, so this can never drift from the match data
    that produced the static report."""
    return [c for c in snapshot["candidate_pool"] if c["status"] == "libib_only"]


def resolved_via_gap_review(gap_decisions: dict[str, Decision]) -> set[str]:
    """libib_keys already confirmed_match-linked to some gap book during a
    *gap*-review session — i.e. the automated matcher missed a real match,
    a human found it from the other direction, and there's nothing left for
    the orphan side to decide. Reuses _claimed_libib_keys with an
    exclude_gap_key that can never match a real (hashed) gap key, since the
    two computations are otherwise identical."""
    return _claimed_libib_keys(gap_decisions, exclude_gap_key="")


def rank_orphan_duplicates(
    orphan: LibibCandidateDict,
    candidate_pool: list[LibibCandidateDict],
    limit: int = _DEFAULT_RANK_LIMIT,
    score_floor: float = _DEFAULT_RANK_FLOOR,
) -> list[dict]:
    """Top-N other Libib entries by title similarity to this orphan — same
    scoring as rank_candidates(), but anchored on another candidate_pool
    entry's own title/creators instead of a gap book, and excluding only
    the orphan itself (not "claimed" entries: a duplicate candidate being
    already matched to something else is exactly the case this is meant to
    catch, e.g. two Libib records for the same book where only one is still
    actually owned)."""
    scored = []
    for c in candidate_pool:
        if c["key"] == orphan["key"]:
            continue
        score = title_score(orphan["title"], c["title"])
        if score < score_floor:
            continue
        overlap = bool(
            orphan["creators"]
            and c["creators"]
            and author_overlap(orphan["creators"], c["creators"])
        )
        scored.append((score, overlap, c))

    scored.sort(key=lambda t: (-t[0], not t[1], t[2]["key"]))
    return [
        {"candidate": c, "score": score, "author_overlap": overlap}
        for score, overlap, c in scored[:limit]
    ]


def search_orphan_duplicates(
    query: str,
    candidate_pool: list[LibibCandidateDict],
    exclude_key: str,
    limit: int = _DEFAULT_SEARCH_LIMIT,
) -> tuple[list[LibibCandidateDict], int]:
    """Plain substring match over title+author across the WHOLE pool, same
    guaranteed fallback role as search_candidates() — only excludes the
    orphan's own key, not anything "claimed" (see rank_orphan_duplicates)."""
    needle = _normalize(query)
    matches = [
        c
        for c in candidate_pool
        if c["key"] != exclude_key
        and (needle in _normalize(c["title"]) or needle in _normalize(c["creators"]))
    ]
    return matches[:limit], len(matches)


def finalize_review(snapshot_path: str, output_dir: str) -> tuple[str, Optional[str]]:
    """Regenerate the reviewed gap CSV (confirmed-match and skipped gap
    books excluded — undecided books stay in by default, matching "never
    silently drop anything") plus a tag-suggestions report for every
    confirmed match. Both reuse existing output.py writers completely
    unchanged; enrichment comes straight from the snapshot, no network
    calls. Safe to re-run repeatedly as more decisions accumulate — each
    run writes a fresh, distinctly-timestamped pair of files rather than
    overwriting the original gap CSV, which stays on disk as an audit
    trail.

    "skipped" is deliberately distinct from "confirmed_match": a skip means
    the book was never in Libib and never should be (a library loan, a
    short story not worth cataloging) — excluded from the gap CSV like a
    match, but with no tag-suggestion, since there's no Libib entry to tag.
    """
    snapshot = load_review_snapshot(snapshot_path)
    decisions = load_decisions(output_dir)
    candidates_by_key = {c["key"]: c for c in snapshot["candidate_pool"]}

    survivors: list[tuple[ScrapedBookResult, EnrichmentResult]] = []
    suggestions: list[tuple[str, str, str]] = []

    for g in snapshot["gap_books"]:
        decision = decisions.get(g["key"])
        if decision and decision.status == "confirmed_match":
            libib = candidates_by_key.get(decision.libib_key or "")
            if libib is not None:
                suggestions.append((g["provider"], libib["title"], libib["creators"]))
            continue  # excluded from the reviewed gap CSV either way

        if decision and decision.status == "skipped":
            continue  # not wanted in Libib at all — no tag suggestion either

        scraped_result = ScrapedBookResult(
            g["provider"],
            (g["title"], g["author"], g["isbn"], g["cover_url"]),
            "missing_from_libib",
        )
        survivors.append((scraped_result, _enrichment_from_dict(g["enrichment"])))

    # Reuses write_gap_csv's own "reconcile_{timestamp}_gap.csv" naming by
    # folding a "_reviewed" suffix into the timestamp argument itself,
    # rather than modifying write_gap_csv — keeps it genuinely unchanged
    # while still producing a self-evidently-distinct filename.
    finalize_ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    gap_csv_path = write_gap_csv(survivors, output_dir, f"{finalize_ts}_reviewed")
    tag_report_path = write_tag_suggestions_report(suggestions, output_dir, finalize_ts)

    return gap_csv_path, tag_report_path


def finalize_orphan_review(snapshot_path: str, output_dir: str) -> Optional[str]:
    """Write a plain-text action report for orphans reviewed as "duplicate"
    or "needs_archive" — "keep" and undecided orphans produce no line, since
    there's nothing actionable to tell the user yet. Same "only ever report,
    never write back to Libib" posture as write_tag_suggestions_report():
    this tells the user exactly what manual edit to make, it doesn't make
    it. Returns None if nothing has been decided yet, matching
    write_orphan_report()'s own Optional[str] convention."""
    snapshot = load_review_snapshot(snapshot_path)
    decisions = load_orphan_decisions(output_dir)
    candidates_by_key = {c["key"]: c for c in snapshot["candidate_pool"]}

    lines: list[str] = []
    for orphan in list_orphans(snapshot):
        decision = decisions.get(orphan["key"])
        if decision is None:
            continue

        label = f'"{orphan["title"]}" by {orphan["creators"]}'
        if decision.status == "duplicate":
            dup = candidates_by_key.get(decision.libib_key or "")
            dup_label = (
                f'"{dup["title"]}" by {dup["creators"]}' if dup else "another entry"
            )
            lines.append(f"Archive (duplicate of {dup_label}): {label}")
        elif decision.status == "needs_archive":
            lines.append(f"Archive (no longer owned): {label}")
        # "keep": reviewed, no action needed — no line.

    if not lines:
        return None

    finalize_ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"reconcile_{finalize_ts}_orphan_review.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("Orphans reviewed — Libib edits to make by hand\n")
        f.write("=" * 44 + "\n\n")
        for line in lines:
            f.write(line + "\n")

    return path
