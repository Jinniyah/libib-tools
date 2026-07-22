# lib/enricher.py — metadata + series enrichment for scraped books.

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests

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

_METADATA_FIELDS = ("description", "publisher", "publish_date", "length_of")


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
) -> Optional[dict]:
    """GET a URL and return parsed JSON, with retry/backoff. None on 404 or exhaustion."""
    max_retries = 3
    backoff = 2

    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=20)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning(
                "HTTP error fetching '%s' (attempt %d/%d): %s",
                context,
                attempt,
                max_retries,
                exc,
            )
            time.sleep(backoff)
            backoff *= 2

    return None


def _first(items: Optional[list]) -> Optional[str]:
    return items[0] if items else None


# -----------------------------
# Open Library metadata
# -----------------------------


def _fetch_open_library(isbn13: Optional[str], title: str, author: str) -> dict:
    """Fetch description/publisher/publish_date/length_of/ISBNs from Open Library."""
    result: dict = {}
    work_key: Optional[str] = None

    edition = None
    if isbn13:
        edition = _http_get_json(_OL_ISBN_URL.format(isbn=isbn13), context=title)
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
        docs = _ol_query(params, title)
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
        work = _http_get_json(_OL_WORKS_URL.format(key=work_key), context=title)
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
    isbn13: Optional[str], title: str, author: str
) -> dict:
    """Fetch metadata from the public (no-auth) Google Books volumes endpoint."""
    if isbn13:
        query = f"isbn:{isbn13}"
    else:
        query = f"intitle:{title}"
        if author:
            query += f"+inauthor:{author}"

    data = _http_get_json(_GOOGLE_BOOKS_URL, context=title, params={"q": query})
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


def _wikidata_query(sparql: str, context: str) -> list[dict]:
    data = _http_get_json(
        _WIKIDATA_URL,
        context=context,
        params={"query": sparql, "format": "json"},
        headers=_WIKIDATA_HEADERS,
    )
    if not data:
        return []
    return data.get("results", {}).get("bindings", [])


def _fetch_wikidata_series(
    isbn13: Optional[str], title: str, author: str
) -> tuple[Optional[str], Optional[int]]:
    """Look up series name + ordinal via Wikidata SPARQL, by ISBN-13 then title+author."""
    bindings: list[dict] = []

    if isbn13:
        bindings = _wikidata_query(_WIKIDATA_ISBN_QUERY.format(isbn=isbn13), title)

    if not bindings and title and author:
        escaped_title = title.replace('"', '\\"')
        escaped_author = author.replace('"', '\\"')
        bindings = _wikidata_query(
            _WIKIDATA_TITLE_QUERY.format(title=escaped_title, author=escaped_author),
            title,
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
    if provider == "openai":
        return _fetch_openai_metadata(title, author, isbn)

    log.warning("Unknown AI_PROVIDER '%s' — skipping AI fallback.", provider)
    return {}


def _fetch_openai_metadata(title: str, author: str, isbn: Optional[str]) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        log.warning(
            "AI_PROVIDER=openai set but OPENAI_API_KEY is missing — skipping AI fallback."
        )
        return {}

    prompt = (
        "Return ONLY a JSON object with these keys: description, publisher, "
        "publish_date, page_count. Use null for any field you are not confident "
        "about. Do not include any other text.\n\n"
        f"Book title: {title}\n"
        f"Author: {author}\n"
        f"ISBN: {isbn or 'unknown'}\n"
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
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
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
) -> EnrichmentResult:
    """Orchestrate Open Library -> Google Books -> AI fallback -> Wikidata series."""
    ol = _fetch_open_library(isbn13, title, author)
    sleep_between_requests()

    missing = [f for f in _METADATA_FIELDS if not ol.get(f)]
    gb: dict = {}
    if missing:
        gb = _fetch_google_books_metadata(isbn13, title, author)
        sleep_between_requests()

    ai: dict = {}
    provider = os.environ.get("AI_PROVIDER")
    still_missing = [f for f in _METADATA_FIELDS if not (ol.get(f) or gb.get(f))]
    if provider and still_missing:
        ai = _fetch_ai_metadata(provider, title, author, isbn13 or isbn10)

    def pick(field: str) -> Optional[str]:
        return ol.get(field) or gb.get(field) or ai.get(field)

    series_name, series_position = _fetch_wikidata_series(isbn13, title, author)

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
