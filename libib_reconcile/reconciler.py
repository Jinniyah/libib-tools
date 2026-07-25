# libib_reconcile/reconciler.py
#
# Matching engine: compares classified Libib entries (libib_reader.LibibEntry)
# against scraped books from each provider, and decides what's already in
# Libib, what's missing, and what needs a human to look at it.
#
# Two-pass, two-pool consumption model (see docs/CLAUDE.md for the full
# rationale): ISBN-exact matching runs first and is provider-agnostic — an
# ISBN doesn't care what platform it's on, and this also lets ambiguous
# "digital"-only entries get resolved without ever needing to guess a
# provider. It also runs even for entries that would otherwise be skipped
# entirely (no digital tag at all, e.g. added to Libib without ever being
# tagged) — an ISBN match is authoritative regardless of tags, so checking
# it first can only rescue matches, never produce a false one. Fuzzy
# title/author matching runs second and is provider-scoped —
# only checked against the providers actually named on that Libib entry's
# tags — and is a *greedy* best-score assignment, not an optimal bipartite
# matching solver: at personal-library scale (low hundreds of gap candidates,
# not thousands), a Hungarian-algorithm-grade solution would be complexity
# with no real payoff. A match "stolen" by a slightly-higher-scoring rival is
# exactly what the low-confidence report exists to catch on human review.

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional

from lib import classify_identifier, dedupe_books_by_title
from lib.openlibrary import _title_is_plausible

from libib_reconcile.libib_reader import LibibEntry

# A scraped book as produced by any scraper's resolve_isbns() step, before
# enrichment: (title, author, isbn, cover_url).
ScrapedBook = tuple[str, str, Optional[str], str]


@dataclass
class MatchResult:
    """The outcome for one Libib entry."""

    entry: LibibEntry
    provider: Optional[str]
    book: Optional[ScrapedBook]
    confidence: Optional[str]  # "high" | "medium" | "low" | None
    method: Optional[str]  # "exact_isbn" | "fuzzy_title_author" | "title_only" | None
    status: str  # "matched" | "libib_only" | "ambiguous" | "out_of_scope"


@dataclass
class ScrapedBookResult:
    """The outcome for one scraped book."""

    provider: str
    book: ScrapedBook
    status: str  # "matched" | "missing_from_libib"


@dataclass
class ReconcileResult:
    libib_results: list[MatchResult]
    scraped_results: list[ScrapedBookResult]


_PACK_SEP = "\x1f"  # unit separator; won't collide with real titles/covers


def _dedupe_scraped_books(books: list[ScrapedBook]) -> list[ScrapedBook]:
    """Dedupe via lib.dedupe_books_by_title, which is typed for 3-tuples of
    (title, author, cover) and only ever inspects `author` (for tie-breaking
    when an existing entry has no author) — the 3rd field is passed through
    untouched. So isbn and cover are packed together into that slot and
    unpacked again afterward, rather than reattaching cover via an isbn-keyed
    dict: many scraped books share an empty/missing isbn (unresolved yet),
    and a dict keyed on isbn would silently collide and drop covers for all
    but one of them.
    """
    packed = [
        (title, author, f"{isbn or ''}{_PACK_SEP}{cover}")
        for title, author, isbn, cover in books
    ]
    deduped = dedupe_books_by_title(packed)

    result: list[ScrapedBook] = []
    for title, author, packed_field in deduped:
        isbn, _, cover = packed_field.partition(_PACK_SEP)
        result.append((title, author, isbn or None, cover))
    return result


def _isbn_match(entry: LibibEntry, scraped_isbn: Optional[str]) -> bool:
    """True if a scraped book's ISBN matches either identifier on a Libib entry."""
    if not scraped_isbn:
        return False
    upc_isbn10, ean_isbn13 = classify_identifier(scraped_isbn)
    if ean_isbn13 and entry.ean_isbn13 and ean_isbn13 == entry.ean_isbn13:
        return True
    if upc_isbn10 and entry.upc_isbn10 and upc_isbn10 == entry.upc_isbn10:
        return True
    return False


def author_overlap(a: str, b: str) -> bool:
    """Loose author corroboration: any shared word longer than 2 characters."""
    a_words = {w for w in re.sub(r"[^\w\s]", "", a.lower()).split() if len(w) > 2}
    b_words = {w for w in re.sub(r"[^\w\s]", "", b.lower()).split() if len(w) > 2}
    return bool(a_words & b_words)


def title_score(a: str, b: str) -> float:
    """Raw title similarity, with no plausibility gate applied — public
    (not just find_fuzzy_match's internal detail) since the interactive
    review feature (libib_reconcile/review.py) reuses it directly to rank
    "maybe" candidates the automated matcher's _title_is_plausible() gate
    rejected outright."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def reconcile(
    libib_entries: list[LibibEntry],
    scraped_books: dict[str, list[ScrapedBook]],
) -> ReconcileResult:
    """Match Libib entries against scraped books from each provider.

    `scraped_books` is keyed by provider name ("kindle", "kobo", "chirp",
    "nook", "google", ...) — any subset of known providers is fine; a
    provider with no scrape supplied is simply treated as having no books.
    """
    pools: dict[str, list[ScrapedBook]] = {
        provider: _dedupe_scraped_books(books)
        for provider, books in scraped_books.items()
    }
    consumed: set[tuple[str, int]] = set()

    def find_isbn_match(entry: LibibEntry) -> Optional[tuple[str, int, ScrapedBook]]:
        # Provider-agnostic on purpose — see module docstring.
        for provider, books in pools.items():
            for idx, book in enumerate(books):
                if (provider, idx) in consumed:
                    continue
                if _isbn_match(entry, book[2]):
                    return provider, idx, book
        return None

    def find_fuzzy_match(
        entry: LibibEntry,
    ) -> Optional[tuple[str, int, ScrapedBook, str]]:
        candidates: list[tuple[float, str, int, ScrapedBook, str]] = []
        for provider in entry.providers:
            for idx, book in enumerate(pools.get(provider, [])):
                if (provider, idx) in consumed:
                    continue
                title, author, _, _ = book
                if not _title_is_plausible(entry.title, title):
                    continue
                confidence = (
                    "medium"
                    if entry.creators
                    and author
                    and author_overlap(entry.creators, author)
                    else "low"
                )
                candidates.append(
                    (title_score(entry.title, title), provider, idx, book, confidence)
                )

        if not candidates:
            return None

        # Sort key must be fully deterministic, not just "highest score
        # first": entry.providers is a set, so the order candidates were
        # appended in (and thus which one a stable sort keeps on a tied
        # score) depended on Python's per-process string hash randomization
        # — confirmed live (2026-07-24): the same inputs produced a
        # different gap list across back-to-back runs whenever a book was
        # owned on two scraped platforms with an identical title score.
        # Breaking ties by provider name then pool index makes the result
        # reproducible regardless of set iteration order.
        candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
        _, provider, idx, book, confidence = candidates[0]
        return provider, idx, book, confidence

    libib_results: list[MatchResult] = []

    for entry in libib_entries:
        # ISBN-exact runs even for skip=True entries, checked *before* the
        # skip cutoff — an ISBN match is authoritative regardless of tags,
        # so there's no false-positive risk in trying it universally. Real
        # gap found live (2026-07-24): 46 entries in a real export had no
        # tags at all (should_skip() == True, e.g. added without ever being
        # tagged), and 38 of those had an ISBN on file that this ordering
        # previously never got a chance to check — contradicting this
        # project's own stated principle of always trying ISBN-exact first.
        isbn_hit = find_isbn_match(entry)
        if isbn_hit:
            provider, idx, book = isbn_hit
            consumed.add((provider, idx))
            libib_results.append(
                MatchResult(entry, provider, book, "high", "exact_isbn", "matched")
            )
            continue

        if entry.skip:
            libib_results.append(
                MatchResult(entry, None, None, None, None, "out_of_scope")
            )
            continue

        # Ambiguous ("digital"-only) entries have no named provider to scope
        # a fuzzy search against, so ISBN was their only shot — see module docstring.
        if entry.providers and entry.providers != {"digital_unknown"}:
            fuzzy_hit = find_fuzzy_match(entry)
            if fuzzy_hit:
                provider, idx, book, confidence = fuzzy_hit
                consumed.add((provider, idx))
                method = (
                    "fuzzy_title_author" if confidence == "medium" else "title_only"
                )
                libib_results.append(
                    MatchResult(entry, provider, book, confidence, method, "matched")
                )
                continue

        status = "ambiguous" if entry.ambiguous else "libib_only"
        libib_results.append(MatchResult(entry, None, None, None, None, status))

    scraped_results: list[ScrapedBookResult] = [
        ScrapedBookResult(
            provider,
            book,
            "matched" if (provider, idx) in consumed else "missing_from_libib",
        )
        for provider, books in pools.items()
        for idx, book in enumerate(books)
    ]

    return ReconcileResult(libib_results=libib_results, scraped_results=scraped_results)
