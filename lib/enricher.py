# lib/enricher.py — metadata + series enrichment for scraped books.

from __future__ import annotations

import json
import logging
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

import requests

from lib.http_retry import request_json
from lib.http_retry import _sleep as _interruptible_sleep
from lib.openlibrary import (
    _best_isbn,
    _ol_query,
    _title_is_plausible,
    sleep_between_requests,
)

log = logging.getLogger(__name__)

_OL_ISBN_URL = "https://openlibrary.org/isbn/{isbn}.json"
_OL_WORKS_URL = "https://openlibrary.org{key}.json"

_GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"

_WIKIDATA_URL = "https://query.wikidata.org/sparql"
_WIKIDATA_HEADERS = {
    "User-Agent": "LibibTools/0.1 (https://github.com/Jinniyah/libib-tools)"
}

_WIKIDATA_ISBN_QUERY = """
SELECT ?seriesLabel ?ordinal WHERE {{
  ?book wdt:P212 "{isbn}".
  ?book p:P179 ?seriesStatement.
  ?seriesStatement ps:P179 ?series.
  OPTIONAL {{ ?seriesStatement pq:P1545 ?ordinal. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
LIMIT 1
"""

_WIKIDATA_TITLE_QUERY = """
SELECT ?seriesLabel ?ordinal WHERE {{
  ?book rdfs:label "{title}"@en.
  ?book wdt:P50 ?author.
  ?author rdfs:label "{author}"@en.
  ?book p:P179 ?seriesStatement.
  ?seriesStatement ps:P179 ?series.
  OPTIONAL {{ ?seriesStatement pq:P1545 ?ordinal. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
LIMIT 1
"""

_OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
_OPENAI_MODEL = "gpt-4o-mini"

# The previous prompt ("use null for any field you are not confident about")
# was too conservative — combined with temperature=0, the model defaulted to
# null for most fields on most books rather than committing to a best-effort
# answer, which is exactly backwards for a last-resort fallback: by the time
# this runs, Open Library and Google Books have both already failed, so *any*
# reasonable estimate beats a blank field. Reworked to explicitly ask for
# best-effort answers, and to specify exact formats (year-only publish date,
# approximate integer page count) instead of leaving the model to guess what
# "confident" means for each field.
_OPENAI_SYSTEM_PROMPT = (
    "You are a bibliographic research assistant helping fill gaps in a "
    "personal library catalog. You are given a book's title and author, "
    "already confirmed correct from the user's own library — use your "
    "general knowledge of this specific title to answer every field with "
    "your best good-faith estimate. Approximate answers are expected and "
    "useful; do not default to null just because you lack an exact figure. "
    "Only use null for a field if you have no relevant knowledge of this "
    "book at all."
)

_METADATA_FIELDS = ("description", "publisher", "publish_date", "length_of")

# Wider, more randomized pacing than the general inter-request delay
# (lib.openlibrary.ISBN_DELAY_RANGE, 0.8-1.6s) — Google's anonymous Books API
# quota is tighter than Open Library's, and got hit with sustained 429s across
# nearly every book at the tighter pacing. Google Books is called for most
# books in practice (only skipped when Open Library already has every field),
# so this delay only slows down the calls that were actually triggering it.
_GOOGLE_BOOKS_DELAY_RANGE = (1.0, 10.0)


def _sleep_after_google_books(cancel_fn: Optional[Callable[[], bool]] = None) -> None:
    _interruptible_sleep(random.uniform(*_GOOGLE_BOOKS_DELAY_RANGE), cancel_fn)


# Circuit breaker: once Google's anonymous quota trips, a few seconds of
# backoff per book does nothing — every subsequent book just rediscovers the
# same block for the cost of 3 wasted retries each. The moment we see one
# 429, stop calling Google Books entirely for this cooldown window instead
# of re-learning the same lesson on every remaining book. Process-wide
# (module-level) rather than per-run: the actual quota is per-IP, so sharing
# it across jobs in this one server process matches reality.
_GOOGLE_BOOKS_COOLDOWN_SECONDS = 300  # 5 minutes — a guess; widen if this
# still isn't enough (Google's public/anonymous quota window is undocumented
# and may reset daily rather than every few minutes — see the response-body
# detail now logged on each 429 in lib/http_retry.py for a real reason code).
_google_books_blocked_until = 0.0


def _google_books_in_cooldown() -> bool:
    return time.time() < _google_books_blocked_until


def _trip_google_books_circuit_breaker() -> None:
    global _google_books_blocked_until
    if not _google_books_in_cooldown():
        log.warning(
            "Google Books rate-limited — pausing Google Books lookups for "
            "the next %.0f minutes instead of retrying per book.",
            _GOOGLE_BOOKS_COOLDOWN_SECONDS / 60,
        )
    _google_books_blocked_until = time.time() + _GOOGLE_BOOKS_COOLDOWN_SECONDS


@dataclass
class EnrichmentResult:
    isbn13: Optional[str] = None
    isbn10: Optional[str] = None
    description: Optional[str] = None
    publisher: Optional[str] = None
    publish_date: Optional[str] = None
    length_of: Optional[str] = None
    series_name: Optional[str] = None
    series_position: Optional[int] = None


# -----------------------------
# Shared HTTP helper
# -----------------------------


def _http_get_json(
    url: str,
    context: str,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    cancel_fn: Optional[Callable[[], bool]] = None,
    max_retries: int = 3,
    on_rate_limited: Optional[Callable[[], None]] = None,
) -> Optional[dict]:
    """GET a URL and return parsed JSON, with retry/backoff. None on 404 or exhaustion."""
    return request_json(
        url,
        context,
        params=params,
        headers=headers,
        max_retries=max_retries,
        cancel_fn=cancel_fn,
        on_rate_limited=on_rate_limited,
    )


def _first(items: Optional[list]) -> Optional[str]:
    return items[0] if items else None


# -----------------------------
# Open Library metadata
# -----------------------------


def _fetch_open_library(
    isbn13: Optional[str],
    title: str,
    author: str,
    cancel_fn: Optional[Callable[[], bool]] = None,
) -> dict:
    """Fetch description/publisher/publish_date/length_of/ISBNs from Open Library."""
    result: dict = {}
    work_key: Optional[str] = None

    edition = None
    if isbn13:
        edition = _http_get_json(
            _OL_ISBN_URL.format(isbn=isbn13), context=title, cancel_fn=cancel_fn
        )
        sleep_between_requests()

    if edition:
        publisher = _first(edition.get("publishers"))
        if publisher:
            result["publisher"] = publisher
        if edition.get("publish_date"):
            result["publish_date"] = edition["publish_date"]
        if edition.get("number_of_pages"):
            result["length_of"] = str(edition["number_of_pages"])
        isbn_10 = _first(edition.get("isbn_10"))
        isbn_13 = _first(edition.get("isbn_13"))
        if isbn_10:
            result["isbn10"] = isbn_10
        if isbn_13:
            result["isbn13"] = isbn_13
        works = edition.get("works") or []
        if works:
            work_key = works[0].get("key")
    else:
        params: dict = {
            "title": title,
            "fields": "key,title,isbn,first_publish_year,publisher,number_of_pages_median",
        }
        if author:
            params["author"] = author
        docs = _ol_query(params, title, cancel_fn=cancel_fn)
        for doc in docs:
            if not _title_is_plausible(title, doc.get("title", "")):
                continue
            publisher = _first(doc.get("publisher"))
            if publisher:
                result["publisher"] = publisher
            if doc.get("first_publish_year"):
                result["publish_date"] = str(doc["first_publish_year"])
            if doc.get("number_of_pages_median"):
                result["length_of"] = str(doc["number_of_pages_median"])
            isbn = _best_isbn(doc.get("isbn") or [])
            if isbn:
                if len(isbn) == 13:
                    result["isbn13"] = isbn
                else:
                    result["isbn10"] = isbn
            work_key = doc.get("key")
            break

    if work_key:
        sleep_between_requests()
        work = _http_get_json(
            _OL_WORKS_URL.format(key=work_key), context=title, cancel_fn=cancel_fn
        )
        if work:
            description = work.get("description")
            if isinstance(description, dict):
                description = description.get("value")
            if description:
                result["description"] = description

    return result


# -----------------------------
# Google Books public metadata
# -----------------------------


def _fetch_google_books_metadata(
    isbn13: Optional[str],
    title: str,
    author: str,
    cancel_fn: Optional[Callable[[], bool]] = None,
) -> dict:
    """Fetch metadata from the public (no-auth) Google Books volumes endpoint."""
    if _google_books_in_cooldown():
        log.info(
            "Skipping Google Books for '%s' — still cooling down after a rate "
            "limit (%.0fs left).",
            title,
            _google_books_blocked_until - time.time(),
        )
        return {}

    if isbn13:
        query = f"isbn:{isbn13}"
    else:
        query = f"intitle:{title}"
        if author:
            query += f"+inauthor:{author}"

    params = {"q": query}
    api_key = os.environ.get("GOOGLE_BOOKS_API_KEY")
    if api_key:
        params["key"] = api_key

    data = _http_get_json(
        _GOOGLE_BOOKS_URL,
        context=title,
        params=params,
        cancel_fn=cancel_fn,
        max_retries=1,  # a rate-limited request doesn't clear in seconds —
        # see _trip_google_books_circuit_breaker rather than burning 3
        # attempts per book re-discovering the same block.
        on_rate_limited=_trip_google_books_circuit_breaker,
    )
    if not data:
        return {}

    result: dict = {}
    for item in data.get("items") or []:
        volume = item.get("volumeInfo", {})
        if not _title_is_plausible(title, volume.get("title", "")):
            continue

        if volume.get("description"):
            result["description"] = volume["description"]
        if volume.get("publisher"):
            result["publisher"] = volume["publisher"]
        if volume.get("publishedDate"):
            result["publish_date"] = volume["publishedDate"]
        if volume.get("pageCount"):
            result["length_of"] = str(volume["pageCount"])

        for ident in volume.get("industryIdentifiers", []):
            id_type = ident.get("type")
            value = ident.get("identifier")
            if id_type == "ISBN_13" and value and "isbn13" not in result:
                result["isbn13"] = value
            elif id_type == "ISBN_10" and value and "isbn10" not in result:
                result["isbn10"] = value
        break

    return result


# -----------------------------
# Wikidata series lookup
# -----------------------------


def _wikidata_query(
    sparql: str, context: str, cancel_fn: Optional[Callable[[], bool]] = None
) -> list[dict]:
    data = _http_get_json(
        _WIKIDATA_URL,
        context=context,
        params={"query": sparql, "format": "json"},
        headers=_WIKIDATA_HEADERS,
        cancel_fn=cancel_fn,
    )
    if not data:
        return []
    return data.get("results", {}).get("bindings", [])


def _fetch_wikidata_series(
    isbn13: Optional[str],
    title: str,
    author: str,
    cancel_fn: Optional[Callable[[], bool]] = None,
) -> tuple[Optional[str], Optional[int]]:
    """Look up series name + ordinal via Wikidata SPARQL, by ISBN-13 then title+author."""
    bindings: list[dict] = []

    if isbn13:
        bindings = _wikidata_query(
            _WIKIDATA_ISBN_QUERY.format(isbn=isbn13), title, cancel_fn=cancel_fn
        )

    if not bindings and title and author:
        escaped_title = title.replace('"', '\\"')
        escaped_author = author.replace('"', '\\"')
        bindings = _wikidata_query(
            _WIKIDATA_TITLE_QUERY.format(title=escaped_title, author=escaped_author),
            title,
            cancel_fn=cancel_fn,
        )

    if not bindings:
        return None, None

    binding = bindings[0]
    series_name = binding.get("seriesLabel", {}).get("value") or None
    ordinal_raw = binding.get("ordinal", {}).get("value")

    position: Optional[int] = None
    if ordinal_raw is not None:
        try:
            position = int(float(ordinal_raw))
        except (TypeError, ValueError):
            position = None

    if not series_name:
        return None, None

    return series_name, position


# -----------------------------
# AI provider fallback (metadata only — see docs/backlog.md AI-1)
# -----------------------------


def _fetch_ai_metadata(
    provider: str, title: str, author: str, isbn: Optional[str]
) -> dict:
    """Dispatch to a provider-specific AI metadata fetcher. Metadata fields only."""
    if provider.strip().lower() == "openai":
        log.info("Trying AI fallback (OpenAI) for '%s'…", title)
        return _fetch_openai_metadata(title, author, isbn)

    log.warning("Unknown AI_PROVIDER '%s' — skipping AI fallback.", provider)
    return {}


def _fetch_openai_metadata(title: str, author: str, isbn: Optional[str]) -> dict:
    # No log line at all on success previously — from the log alone there was
    # no way to tell the AI fallback was even being attempted, let alone
    # whether it worked. Every path below now logs something.
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        log.warning(
            "AI_PROVIDER=openai set but OPENAI_API_KEY is missing — skipping AI fallback."
        )
        return {}

    prompt = (
        f'Book: "{title}" by {author or "unknown author"} (ISBN: {isbn or "unknown"}).\n\n'
        "Return ONLY a JSON object with exactly these keys:\n"
        '- "description": a 1-3 sentence summary of the book, in your own '
        "words — what it's about, not a review.\n"
        '- "publisher": the original or best-known publisher of this book. '
        "A well-informed best guess is fine if you're not 100% certain.\n"
        '- "publish_date": the first publication year ONLY, as a 4-digit '
        "number (e.g. 1993) — not a full date, not the ISBN edition's year.\n"
        '- "page_count": your best approximate page count for a typical '
        "print edition, as an integer (e.g. 320) — an estimate rounded to "
        "the nearest 10-20 pages is fine, it does not need to be exact.\n\n"
        "No other text, no markdown formatting — just the JSON object."
    )

    try:
        resp = requests.post(
            _OPENAI_CHAT_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": _OPENAI_MODEL,
                "messages": [
                    {"role": "system", "content": _OPENAI_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0,
                # Enforces a valid JSON object back — without this, a chat
                # model will sometimes wrap the JSON in a ```json fence or
                # add a sentence despite being told not to, which either
                # fails to parse (caught below) or, worse, parses but under
                # different key names than expected, silently producing no
                # usable fields with no error at all.
                "response_format": {"type": "json_object"},
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
    except Exception as exc:
        log.warning("AI metadata fallback failed for '%s': %s", title, exc)
        return {}

    result: dict = {}
    if data.get("description"):
        result["description"] = str(data["description"])
    if data.get("publisher"):
        result["publisher"] = str(data["publisher"])
    if data.get("publish_date"):
        result["publish_date"] = str(data["publish_date"])
    if data.get("page_count"):
        result["length_of"] = str(data["page_count"])

    if not result:
        # Visibility for exactly the case that prompted this: parsing
        # succeeded (no exception above) but nothing matched our expected
        # keys — could be the model genuinely being unsure (nulls across the
        # board) or returning different key names than requested. Log the
        # raw content so that's diagnosable instead of a silent "no fields."
        log.info("AI fallback (OpenAI) raw response for '%s': %s", title, content)

    if result:
        log.info(
            "AI fallback (OpenAI) filled %d field(s) for '%s'.", len(result), title
        )
    else:
        log.info("AI fallback (OpenAI) returned no usable fields for '%s'.", title)

    return result


# -----------------------------
# Public API
# -----------------------------


def enrich_book(
    title: str,
    author: str,
    isbn13: Optional[str],
    isbn10: Optional[str],
    existing_notes: str,
    cancel_fn: Optional[Callable[[], bool]] = None,
) -> EnrichmentResult:
    """Orchestrate Open Library -> Google Books -> AI fallback -> Wikidata series."""
    ol = _fetch_open_library(isbn13, title, author, cancel_fn=cancel_fn)
    sleep_between_requests()

    missing = [f for f in _METADATA_FIELDS if not ol.get(f)]
    gb: dict = {}
    if missing:
        gb = _fetch_google_books_metadata(isbn13, title, author, cancel_fn=cancel_fn)
        _sleep_after_google_books(cancel_fn=cancel_fn)

    ai: dict = {}
    provider = os.environ.get("AI_PROVIDER")
    still_missing = [f for f in _METADATA_FIELDS if not (ol.get(f) or gb.get(f))]
    if provider and still_missing:
        ai = _fetch_ai_metadata(provider, title, author, isbn13 or isbn10)

    def pick(field: str) -> Optional[str]:
        return ol.get(field) or gb.get(field) or ai.get(field)

    series_name, series_position = _fetch_wikidata_series(
        isbn13, title, author, cancel_fn=cancel_fn
    )

    return EnrichmentResult(
        isbn13=isbn13 or ol.get("isbn13") or gb.get("isbn13"),
        isbn10=isbn10 or ol.get("isbn10") or gb.get("isbn10"),
        description=pick("description"),
        publisher=pick("publisher"),
        publish_date=pick("publish_date"),
        length_of=pick("length_of"),
        series_name=series_name,
        series_position=series_position,
    )


def format_series_notes(
    series_name: Optional[str],
    series_position: Optional[int],
    existing_notes: str,
) -> str:
    """Format the 'Series: <name> #<pos> || Additional Notes: <notes>' prefix."""
    if not series_name:
        return existing_notes

    position_str = f"{series_position:03d}" if series_position is not None else "ZZZ"
    return (
        f"Series: {series_name} #{position_str} || Additional Notes: {existing_notes}"
    )
