# lib/openlibrary.py

from __future__ import annotations
import logging
import random
import re
import time
from collections.abc import Callable
from difflib import SequenceMatcher
from typing import Optional

from lib.http_retry import request_json

log = logging.getLogger(__name__)

# Base Open Library endpoint
_OL_URL = "https://openlibrary.org/search.json"

# Default parameters for all queries
_OL_BASE_PARAMS = {
    "mode": "everything",
    "limit": 5,
    "fields": "title,isbn",
}

# Open Library's own API docs: unidentified requests are capped at 1/s;
# requests with a User-Agent identifying the app get 3/s. Sending this
# doesn't just raise our ceiling — Open Library's docs specifically warn
# that unidentified bulk traffic is more likely to be throttled/blocked
# outright, which lines up with the 503 storms seen in practice.
_OL_HEADERS = {"User-Agent": "LibibTools/0.1 (https://github.com/Jinniyah/libib-tools)"}

# Randomized delay range (seconds)
ISBN_DELAY_RANGE = (0.8, 1.6)

# Circuit breaker: Open Library's search endpoint has been observed to
# return 503 ("no server available") in multi-minute storms that clear on
# their own, interspersed with otherwise-clean requests — a backend
# capacity issue (see e.g. internetarchive/openlibrary#6804), not a hard
# quota block like Google Books'. Once a request exhausts every retry
# against one of these storms, skip Open Library entirely for a short
# cooldown instead of every subsequent book (there can be thousands, per a
# full Kindle library) independently re-paying the same ~30s retry tax to
# rediscover the same outage. Short relative to Google's 5-minute cooldown
# since these storms have been observed to clear in well under that.
_OL_COOLDOWN_SECONDS = 90.0
_ol_blocked_until = 0.0


def _ol_in_cooldown() -> bool:
    return time.time() < _ol_blocked_until


def _ol_cooldown_remaining() -> float:
    return max(0.0, _ol_blocked_until - time.time())


def _trip_ol_circuit_breaker() -> None:
    global _ol_blocked_until
    if not _ol_in_cooldown():
        log.warning(
            "Open Library failed repeatedly (likely a temporary outage) — "
            "pausing Open Library lookups for %.0fs.",
            _OL_COOLDOWN_SECONDS,
        )
    _ol_blocked_until = time.time() + _OL_COOLDOWN_SECONDS


# -----------------------------
# Libib CSV schema
# -----------------------------

# Full ordered column list for Libib CSV import.
LIBIB_HEADERS: list[str] = [
    "added",
    "creators",
    "began_date",
    "call_numbers",
    "completed_date",
    "copies",
    "description",
    "group",
    "upc_isbn10",
    "ean_isbn13",
    "ddc",
    "lcc",
    "lccn",
    "oclc",
    "lexile",
    "length_of",
    "number_of_discs",
    "aspect_ratio",
    "notes",
    "price",
    "publish_date",
    "publisher",
    "rating",
    "review",
    "review_date",
    "status",
    "tags",
    "title",
]


def classify_identifier(identifier: str) -> tuple[str, str]:
    """Return ``(upc_isbn10, ean_isbn13)`` for a raw identifier string.

    Classification rules:
    - 13 digits (after stripping hyphens) → ISBN-13 / EAN → ``ean_isbn13``
    - 10 characters (after stripping hyphens, digits + optional trailing X) → ISBN-10 / UPC → ``upc_isbn10``
    - Anything else → placed in ``upc_isbn10`` as a best-effort fallback
    - Empty string → both fields empty
    """
    stripped = identifier.strip()
    if not stripped:
        return "", ""
    digits_only = stripped.replace("-", "")
    if len(digits_only) == 13 and digits_only.isdigit():
        return "", stripped
    if len(digits_only) == 10:
        return stripped, ""
    # Unknown format — best-effort fallback
    return stripped, ""


# -----------------------------
# ISBN Normalization & Validation
# -----------------------------


def _normalize_isbn(raw: str) -> str:
    return re.sub(r"[^0-9Xx]", "", raw or "").upper()


def _valid_isbn13(s: str) -> bool:
    if len(s) != 13 or not s.isdigit():
        return False
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(s))
    return total % 10 == 0


def _valid_isbn10(s: str) -> bool:
    if len(s) != 10 or not s[:-1].isdigit() or not (s[-1].isdigit() or s[-1] == "X"):
        return False
    # X is only legal in position 9 (last); value is 10
    total = sum(
        (10 - i) * (10 if (i == 9 and s[i] == "X") else int(s[i])) for i in range(10)
    )
    return total % 11 == 0


def _best_isbn(isbns: list[str]) -> Optional[str]:
    """Return the first ISBN-13, then ISBN-10, or None."""
    normed = [_normalize_isbn(i) for i in isbns if i]
    for i in normed:
        if _valid_isbn13(i):
            return i
    for i in normed:
        if _valid_isbn10(i):
            return i
    return None


# -----------------------------
# Title Matching
# -----------------------------


def _title_is_plausible(
    query_title: str, returned_title: str, threshold: float = 0.55
) -> bool:
    """
    Determine whether a returned title plausibly matches the query.
    """
    q = re.sub(r"[^\w\s]", "", query_title.lower()).strip()
    r = re.sub(r"[^\w\s]", "", returned_title.lower()).strip()

    if SequenceMatcher(None, q, r).ratio() >= threshold:
        return True

    if q and q in r:
        return True

    q_words = {w for w in q.split() if len(w) > 3}
    r_words = set(r.split())
    if q_words and len(q_words & r_words) / len(q_words) >= 0.60:
        return True

    return False


# -----------------------------
# Open Library Query with Retry
# -----------------------------


def _ol_query(
    params: dict,
    title_for_log: str,
    cancel_fn: Optional[Callable[[], bool]] = None,
) -> list[dict]:
    """Execute one Open Library search with retries and exponential backoff."""
    if _ol_in_cooldown():
        log.info(
            "Skipping Open Library for '%s' — still cooling down after "
            "repeated errors (%.0fs left).",
            title_for_log,
            _ol_cooldown_remaining(),
        )
        return []

    data = request_json(
        _OL_URL,
        title_for_log,
        params={**_OL_BASE_PARAMS, **params},
        headers=_OL_HEADERS,
        max_retries=4,
        cancel_fn=cancel_fn,
        on_exhausted=_trip_ol_circuit_breaker,
    )
    return (data or {}).get("docs", [])


# -----------------------------
# ISBN Selection Logic
# -----------------------------


def _pick_isbn_from_docs(docs: list[dict], title: str) -> Optional[str]:
    """Two-pass ISBN selection."""
    # Pass 1: title plausibility check
    for doc in docs:
        if _title_is_plausible(title, doc.get("title", "")):
            isbn = _best_isbn(doc.get("isbn") or [])
            if isbn:
                return isbn

    # Pass 2: accept any valid ISBN
    for doc in docs:
        isbn = _best_isbn(doc.get("isbn") or [])
        if isbn:
            return isbn

    return None


# -----------------------------
# Public API
# -----------------------------


def get_isbn(
    title: str, author: str, cancel_fn: Optional[Callable[[], bool]] = None
) -> Optional[str]:
    """
    Look up an ISBN via Open Library using:
    1. title + author
    2. title only
    """
    # Pass 1: title + author
    if author:
        docs = _ol_query({"title": title, "author": author}, title, cancel_fn=cancel_fn)
        isbn = _pick_isbn_from_docs(docs, title)
        if isbn:
            return isbn

    # Pass 2: title only
    docs = _ol_query({"title": title}, title, cancel_fn=cancel_fn)
    isbn = _pick_isbn_from_docs(docs, title)
    if isbn:
        return isbn

    return None


def sleep_between_requests() -> None:
    """Randomized delay to avoid rate-limiting."""
    time.sleep(random.uniform(*ISBN_DELAY_RANGE))


def dedupe_books_by_title(
    books: list[tuple[str, str, str]],
    dropped: Optional[list[tuple[str, str, str]]] = None,
) -> list[tuple[str, str, str]]:
    """Remove duplicate titles, preferring entries that have an author.

    Grouped by title, but two entries sharing a title are only treated as
    the *same* book if their authors match (or one side's author is blank —
    recovering from the same book being scraped twice where one copy's
    author extraction glitched to blank). Two different, non-blank authors
    under the same title are kept as distinct books rather than merged —
    confirmed as a real, live false-positive: 'Apex' by Mercedes Lackey was
    getting silently dropped as a "duplicate" of the unrelated 'Apex' by
    Seth Ring under the previous title-only key.

    If ``dropped`` is given, every entry actually removed (not merely
    replaced — a blank-author entry being swapped for a better copy of the
    same book isn't a loss) is appended to it as (title, author, reason), so
    a caller can write out a human-reviewable report rather than relying on
    scrollback logs — false positives here are silent by nature (a "removed"
    book just isn't in the output) unless someone can see what was dropped.
    """
    seen: dict[str, list[int]] = {}
    unique: list[tuple[str, str, str]] = []
    removed = replaced = 0

    for title, author, cover in books:
        key = (title or "").strip().lower()
        author_norm = (author or "").strip().lower()

        match_idx = None
        for idx in seen.get(key, []):
            _, existing_author, _ = unique[idx]
            existing_author_norm = (existing_author or "").strip().lower()
            if (
                not existing_author_norm
                or not author_norm
                or existing_author_norm == author_norm
            ):
                match_idx = idx
                break

        if match_idx is None:
            seen.setdefault(key, []).append(len(unique))
            unique.append((title, author, cover))
            continue

        existing_title, existing_author, _ = unique[match_idx]
        if not existing_author and author:
            log.info(
                "Deduplication: replacing blank-author entry '%s' with "
                "'%s' by '%s'.",
                existing_title,
                title,
                author,
            )
            unique[match_idx] = (title, author, cover)
            replaced += 1
        else:
            log.info(
                "Deduplication: dropping '%s' by '%s' as a duplicate of "
                "'%s' by '%s'.",
                title,
                author,
                existing_title,
                existing_author,
            )
            if dropped is not None:
                dropped.append(
                    (
                        title,
                        author,
                        f"Deduplication: duplicate of '{existing_title}' by "
                        f"'{existing_author}'",
                    )
                )
            removed += 1

    if removed or replaced:
        log.info("Deduplication: %d removed, %d replaced.", removed, replaced)
    return unique


def filter_invalid_books(
    books: list[tuple[str, str, str]],
    extra_garbage: frozenset[str] = frozenset(),
    dropped: Optional[list[tuple[str, str, str]]] = None,
) -> list[tuple[str, str, str]]:
    """
    Drop entries with missing, trivial, or garbage titles.

    Pass ``extra_garbage`` to add platform-specific junk titles
    (e.g. Kindle UI strings) without touching shared logic.

    If ``dropped`` is given, every removed entry is appended to it as
    (title, author, reason) — see dedupe_books_by_title's docstring for why:
    a filtered-out book is a silent loss unless something records what was
    dropped and why, for a human to check for false positives.
    """
    _BASE_GARBAGE = frozenset({"audiobook", "book", "ebook"})
    garbage = _BASE_GARBAGE | extra_garbage

    valid: list[tuple[str, str, str]] = []
    removed = 0

    for title, author, cover in books:
        t = (title or "").strip()
        if not t or len(re.findall(r"[A-Za-z0-9]", t)) < 2:
            reason = (
                "Filter: blank, or fewer than 2 alphanumeric characters "
                "(also rejects titles entirely in non-Latin script)"
            )
            log.info("Filtered invalid title (%s): %r by %r", reason, title, author)
            if dropped is not None:
                dropped.append((title, author, reason))
            removed += 1
            continue
        if t.lower() in garbage:
            reason = f"Filter: matched a generic placeholder word '{t.lower()}'"
            log.info("Filtered invalid title (%s): %r by %r", reason, title, author)
            if dropped is not None:
                dropped.append((title, author, reason))
            removed += 1
            continue
        if re.match(r"^[\W_]+$", t):
            reason = "Filter: no word characters at all"
            log.info("Filtered invalid title (%s): %r by %r", reason, title, author)
            if dropped is not None:
                dropped.append((title, author, reason))
            removed += 1
            continue
        valid.append((title, author, cover))

    if removed:
        log.info("Filtered %d invalid book(s) before ISBN lookup.", removed)
    return valid
