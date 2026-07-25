# libib_reconcile/libib_reader.py
#
# Parses and classifies a real Libib *export* CSV. This is a different schema
# from lib.LIBIB_HEADERS (the *import* schema scrapers write) — notably
# `length`/`began`/`completed` here vs. `length_of`/`began_date`/`completed_date`
# on import, plus several export-only columns. Do not conflate the two.

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Column order as it appears in a real Libib export (Settings -> Export).
LIBIB_EXPORT_HEADERS: list[str] = [
    "item_type",
    "title",
    "creators",
    "first_name",
    "last_name",
    "collection",
    "ean_isbn13",
    "upc_isbn10",
    "description",
    "publisher",
    "publish_date",
    "group",
    "tags",
    "notes",
    "price",
    "length",
    "number_of_discs",
    "number_of_players",
    "age_group",
    "ensemble",
    "aspect_ratio",
    "esrb",
    "rating",
    "review",
    "review_date",
    "status",
    "began",
    "completed",
    "added",
    "copies",
]

# Tag keywords that map to a scraper provider. "audiobook" is included because
# Chirp entries are commonly tagged "audiobook, chirp" rather than "chirp" alone.
# "bn" (Barnes & Noble's own abbreviation) is included alongside "nook" because
# real export data has both — confirmed live (2026-07-24): 6 entries tagged
# "bn" instead of "nook", none tagged both, so this isn't a redundant alias —
# without it those entries had no recognized provider at all and were
# invisible to Nook's provider-scoped fuzzy matching.
_PROVIDER_KEYWORDS: dict[str, str] = {
    "kindle": "kindle",
    "kobo": "kobo",
    "chirp": "chirp",
    "audiobook": "chirp",
    "nook": "nook",
    "bn": "nook",
    "google": "google",
}

# Any of these present means the entry represents a digital copy of some kind.
_DIGITAL_KEYWORDS: frozenset[str] = frozenset(
    {"kindle", "kobo", "chirp", "audiobook", "nook", "bn", "google", "digital"}
)

# Columns a usable export must have; anything else is tolerated (Libib may add
# optional columns over time, and DictReader is order-independent).
_REQUIRED_COLUMNS: frozenset[str] = frozenset(
    {"title", "tags", "ean_isbn13", "upc_isbn10"}
)


@dataclass
class LibibEntry:
    title: str
    creators: str
    tags: set[str]
    providers: set[str]
    ean_isbn13: str
    upc_isbn10: str
    skip: bool
    ambiguous: bool


def normalize_tags(raw: str) -> set[str]:
    """Lowercase, split on ',', strip whitespace, drop empties."""
    return {t.strip() for t in (raw or "").lower().split(",") if t.strip()}


def classify_providers(tags: set[str]) -> set[str]:
    """Detect named providers from tag keywords.

    Returns any of {kindle, kobo, chirp, nook, google} found. If "digital" is
    present but no named provider was found, returns {"digital_unknown"}
    instead — the entry is a digital copy of unknown platform, not physical.
    """
    providers = {_PROVIDER_KEYWORDS[t] for t in tags if t in _PROVIDER_KEYWORDS}
    if not providers and "digital" in tags:
        return {"digital_unknown"}
    return providers


def should_skip(tags: set[str]) -> bool:
    """True if the entry is marked deleted/removed, or has no digital keyword
    at all (i.e. it's a physical-only book, out of scope for reconciliation)."""
    if "deleted" in tags or "removed" in tags:
        return True
    return not (tags & _DIGITAL_KEYWORDS)


def is_ambiguous(tags: set[str], providers: set[str]) -> bool:
    """True if the entry is digital but its platform couldn't be determined."""
    return "digital" in tags and providers == {"digital_unknown"}


def extract_isbns(row: dict[str, str]) -> tuple[str, str]:
    """Pull (ean_isbn13, upc_isbn10) straight from an export row."""
    return (
        (row.get("ean_isbn13") or "").strip(),
        (row.get("upc_isbn10") or "").strip(),
    )


def _read_rows(path: str) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = set(reader.fieldnames or [])
        missing = _REQUIRED_COLUMNS - fieldnames
        if missing:
            log.warning(
                "Libib export at %s is missing expected column(s): %s",
                path,
                ", ".join(sorted(missing)),
            )
        return list(reader)


def read_libib_export(path: str) -> list[LibibEntry]:
    """Parse a Libib export CSV into classified LibibEntry records."""
    entries = []
    for row in _read_rows(path):
        tags = normalize_tags(row.get("tags", ""))
        providers = classify_providers(tags)
        ean_isbn13, upc_isbn10 = extract_isbns(row)

        entries.append(
            LibibEntry(
                title=(row.get("title") or "").strip(),
                creators=(row.get("creators") or "").strip(),
                tags=tags,
                providers=providers,
                ean_isbn13=ean_isbn13,
                upc_isbn10=upc_isbn10,
                skip=should_skip(tags),
                ambiguous=is_ambiguous(tags, providers),
            )
        )

    return entries
