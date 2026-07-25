# libib_reconcile/output.py
#
# All output files from a reconciliation run: a ready-to-import gap CSV plus
# four human-readable .txt reports. Every writer takes an already-computed
# `timestamp` string rather than generating its own, so a single reconcile
# run's output files share one timestamp (per OUT-6) and so each writer is
# independently testable without freezing the clock.

from __future__ import annotations

import csv
import logging
import os
from collections.abc import Callable, Sequence
from typing import Optional, Protocol

from lib import (
    LIBIB_HEADERS,
    EnrichmentResult,
    OperationCancelled,
    classify_identifier,
    enrich_book,
    format_series_notes,
    sleep_between_requests,
)

from libib_reconcile.reconciler import ReconcileResult, ScrapedBookResult

log = logging.getLogger(__name__)

ENRICH_LOG_INTERVAL: int = 25

# Used in place of a real EnrichmentResult when enrichment is skipped.
_NULL_ENRICHMENT = EnrichmentResult()

# Same tag conventions as each scraper's own LIBIB_TYPE constant. Duplicated
# here rather than imported from each scraper's core.py — the reconciler
# shouldn't depend on scraper internals for a handful of static strings.
# Public (not `_PROVIDER_TAGS`): libib_reconcile/review.py's finalize step
# needs it too, for the tag-suggestions report.
PROVIDER_TAGS: dict[str, str] = {
    "chirp": "chirp,audiobook",
    "kindle": "kindle,ebook",
    "kobo": "kobo,ebook",
    "nook": "nook,ebook",
    "google": "google,ebook",
}


def _output_path(directory: str, filename: str) -> str:
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, filename)


def provider_tag(provider: str) -> str:
    return PROVIDER_TAGS.get(provider, provider)


# ==========================
# Gap CSV
# ==========================


def enrich_gap_books(
    gap_books: list[ScrapedBookResult],
    no_enrich: bool = False,
    cancel_fn: Callable[[], bool] = lambda: False,
) -> list[tuple[ScrapedBookResult, EnrichmentResult]]:
    """Run lib.enrich_book() over each gap book, same pattern as every
    scraper's own enrich_books() step. Skipped entirely if no_enrich."""
    if no_enrich:
        return [(r, _NULL_ENRICHMENT) for r in gap_books]

    total = len(gap_books)
    enriched: list[tuple[ScrapedBookResult, EnrichmentResult]] = []

    for idx, r in enumerate(gap_books, start=1):
        if cancel_fn():
            raise OperationCancelled()

        title, author, isbn, cover = r.book
        upc_isbn10, ean_isbn13 = classify_identifier(isbn) if isbn else ("", "")
        result = enrich_book(
            title,
            author,
            ean_isbn13 or None,
            upc_isbn10 or None,
            cover,
            cancel_fn=cancel_fn,
        )
        sleep_between_requests()

        enriched.append((r, result))

        if idx % ENRICH_LOG_INTERVAL == 0 or idx == total:
            log.info("Enrichment progress: %d/%d gap book(s) processed.", idx, total)

    return enriched


def write_gap_csv(
    enriched_gap_books: list[tuple[ScrapedBookResult, EnrichmentResult]],
    output_dir: str,
    timestamp: str,
) -> str:
    """Write the ready-to-import gap CSV — books found in a scrape but not in
    Libib, full 28-column format, one row per book."""
    path = _output_path(output_dir, f"reconcile_{timestamp}_gap.csv")

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=LIBIB_HEADERS)
        writer.writeheader()
        for scraped_result, enrichment in enriched_gap_books:
            title, author, isbn, cover = scraped_result.book
            if isbn:
                upc_isbn10, ean_isbn13 = classify_identifier(isbn)
            else:
                upc_isbn10 = enrichment.isbn10 or ""
                ean_isbn13 = enrichment.isbn13 or ""

            row = {h: "" for h in LIBIB_HEADERS}
            row["title"] = title
            row["creators"] = author
            row["upc_isbn10"] = upc_isbn10
            row["ean_isbn13"] = ean_isbn13
            row["tags"] = provider_tag(scraped_result.provider)
            row["description"] = enrichment.description or ""
            row["publisher"] = enrichment.publisher or ""
            row["publish_date"] = enrichment.publish_date or ""
            row["length_of"] = enrichment.length_of or ""
            row["group"] = enrichment.series_name or ""
            row["notes"] = format_series_notes(
                enrichment.series_name, enrichment.series_position, cover
            )
            writer.writerow(row)

    return path


# ==========================
# Reports
# ==========================


def write_reconciliation_report(
    result: ReconcileResult, output_dir: str, timestamp: str
) -> str:
    """Summary counts + a per-provider breakdown."""
    path = _output_path(output_dir, f"reconcile_{timestamp}_summary.txt")

    libib_results = result.libib_results
    scraped_results = result.scraped_results
    providers = sorted({r.provider for r in scraped_results})

    with open(path, "w", encoding="utf-8") as f:
        f.write("Reconciliation Summary\n")
        f.write("=" * 44 + "\n\n")

        f.write(f"Libib entries considered: {len(libib_results)}\n")
        f.write(f"  Matched:       {_count(libib_results, 'matched')}\n")
        f.write(f"  Orphans:       {_count(libib_results, 'libib_only')}\n")
        f.write(f"  Ambiguous:     {_count(libib_results, 'ambiguous')}\n")
        f.write(f"  Out of scope:  {_count(libib_results, 'out_of_scope')}\n\n")

        f.write(f"Scraped books considered: {len(scraped_results)}\n")
        f.write(f"  Already in Libib:  {_count(scraped_results, 'matched')}\n")
        f.write(
            f"  Missing (gap):     {_count(scraped_results, 'missing_from_libib')}\n\n"
        )

        f.write("Per-provider breakdown\n")
        f.write("-" * 44 + "\n")
        if not providers:
            f.write("(no provider scrapes supplied)\n")
        for provider in providers:
            provider_results = [r for r in scraped_results if r.provider == provider]
            f.write(
                f"{provider}: {_count(provider_results, 'matched')} matched, "
                f"{_count(provider_results, 'missing_from_libib')} missing\n"
            )

    return path


def write_orphan_report(
    result: ReconcileResult, output_dir: str, timestamp: str
) -> Optional[str]:
    """Libib entries not found in any scrape."""
    orphans = [m for m in result.libib_results if m.status == "libib_only"]
    if not orphans:
        return None

    path = _output_path(output_dir, f"reconcile_{timestamp}_orphans.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("Libib entries not found in any scrape\n")
        f.write("=" * 44 + "\n\n")
        for m in orphans:
            providers = ", ".join(sorted(m.entry.providers)) or "unknown provider"
            f.write(f"{m.entry.title}  —  {m.entry.creators}  [{providers}]\n")

    return path


def write_low_confidence_report(
    result: ReconcileResult, output_dir: str, timestamp: str
) -> Optional[str]:
    """Fuzzy matches (medium/low confidence) needing human review."""
    low_confidence = [
        m
        for m in result.libib_results
        if m.status == "matched" and m.confidence in ("medium", "low")
    ]
    if not low_confidence:
        return None

    path = _output_path(output_dir, f"reconcile_{timestamp}_low_confidence.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("Fuzzy matches needing human review\n")
        f.write("=" * 44 + "\n\n")
        for m in low_confidence:
            book = m.book
            assert book is not None  # "matched" always carries a book
            scraped_title, scraped_author, _, _ = book
            f.write(
                f'[{m.confidence}] Libib: "{m.entry.title}" by {m.entry.creators}\n'
                f'           Scraped ({m.provider}): "{scraped_title}" by {scraped_author}\n\n'
            )

    return path


def write_tag_suggestions_report(
    suggestions: list[tuple[str, str, str]],
    output_dir: str,
    timestamp: str,
) -> Optional[str]:
    """One line per gap book a human confirmed is already in Libib, just
    under a different/missing tag — `suggestions` is (provider, libib_title,
    libib_creators), pre-resolved by libib_reconcile/review.py's finalize
    step rather than this module depending on review.py's own data shapes.
    These entries were deliberately excluded from the gap CSV (they're not
    new books to import) — this is the other half of that decision: telling
    the user exactly what manual Libib edit closes the loop. Never write
    back to Libib directly, only ever report."""
    if not suggestions:
        return None

    path = _output_path(output_dir, f"reconcile_{timestamp}_tag_suggestions.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "Books confirmed already in Libib during review — add these "
            "tags by hand\n"
        )
        f.write("=" * 44 + "\n\n")
        for provider, title, creators in suggestions:
            f.write(f"Add tag '{provider}' to: \"{title}\" by {creators}\n")

    return path


def write_ambiguous_report(
    result: ReconcileResult, output_dir: str, timestamp: str
) -> Optional[str]:
    """Libib entries tagged 'digital' with no identifiable provider."""
    ambiguous = [m for m in result.libib_results if m.status == "ambiguous"]
    if not ambiguous:
        return None

    path = _output_path(output_dir, f"reconcile_{timestamp}_ambiguous.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("Digital entries with no identifiable provider\n")
        f.write("=" * 44 + "\n\n")
        for m in ambiguous:
            f.write(f"{m.entry.title}  —  {m.entry.creators}\n")

    return path


class _HasStatus(Protocol):
    status: str


def _count(items: Sequence[_HasStatus], status: str) -> int:
    return sum(1 for item in items if item.status == status)
