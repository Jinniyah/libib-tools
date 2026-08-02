# libib_reconcile/core.py
#
# CLI and orchestration: wires libib_reader -> reconciler -> isbn_enricher ->
# output together with a single shared timestamp, mirroring the run()/main()
# split already established for the four scrapers (see Rec-4a in
# docs/backlog.md and docs/CLAUDE.md's "CLI entry point: run() vs main()").

from __future__ import annotations

import argparse
import csv
import html
import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from libib_reconcile.isbn_enricher import enrich_missing_isbns
from libib_reconcile.libib_reader import read_libib_export
from libib_reconcile.output import (
    enrich_gap_books,
    write_ambiguous_report,
    write_gap_csv,
    write_low_confidence_report,
    write_orphan_report,
    write_reconciliation_report,
)
from libib_reconcile.reconciler import ScrapedBook, reconcile
from libib_reconcile.review import write_review_snapshot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_PROVIDERS: tuple[str, ...] = ("chirp", "kindle", "kobo", "nook", "google")

# Providers with a working scraper run() entry point. Google Books
# (google_to_libib) hasn't been built yet — see Google-1/Google-2 in
# docs/backlog.md. Deliberately imported lazily (importlib, inside
# _gather_scraped_books) rather than at module level, so libib_reconcile
# doesn't force a Selenium import chain just to reconcile pre-existing CSVs.
_SCRAPER_MODULES: dict[str, str] = {
    "chirp": "chirp_to_libib.core",
    "kindle": "kindle_to_libib.core",
    "kobo": "kobo_to_libib.core",
    "nook": "nook_to_libib.core",
}


@dataclass
class ReconcileRunResult:
    gap_csv_path: Optional[str]
    summary_path: Optional[str]
    orphan_path: Optional[str]
    low_confidence_path: Optional[str]
    ambiguous_path: Optional[str]
    total_libib_entries: int
    total_gap_books: int
    review_snapshot_path: Optional[str] = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile a Libib export against your provider scrapes "
        "and produce a ready-to-import gap CSV."
    )
    parser.add_argument(
        "--libib", required=True, metavar="PATH", help="Libib export CSV"
    )
    parser.add_argument(
        "--scrape",
        action="store_true",
        help="Trigger live scrapes for the selected --providers instead of "
        "(or in addition to) reading pre-existing CSVs.",
    )
    for provider in _PROVIDERS:
        parser.add_argument(
            f"--{provider}", metavar="PATH", help=f"Pre-existing {provider} scrape CSV"
        )
    parser.add_argument(
        "--providers",
        nargs="+",
        choices=_PROVIDERS,
        default=list(_PROVIDERS),
        metavar="PROVIDER",
        help="Limit to specific providers (default: all).",
    )
    parser.add_argument("--output-dir", default=".", metavar="PATH")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="Skip metadata/series enrichment on the gap CSV (faster, no extra HTTP calls).",
    )
    parser.add_argument(
        "--wait-for-rate-limits",
        action="store_true",
        help="Wait out an Open Library/Google Books rate-limit cooldown instead of "
        "skipping that source for books hit during it — slower, but nothing is "
        "left without metadata just because of unlucky timing.",
    )
    return parser.parse_args()


def _load_scrape_csv(path: str) -> list[ScrapedBook]:
    """Read a LIBIB_HEADERS-shaped scrape CSV back into (title, author, isbn,
    cover) tuples. This is the interchange format for both --scrape output and
    user-supplied --<provider> PATH files, so both code paths converge here.
    """
    books: list[ScrapedBook] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            # html.unescape() defensively, on top of the scrapers' own fix
            # for this (see kindle_to_libib._element_text) — this path is
            # also how already-scraped CSVs from *before* that fix landed
            # get read back in, and a re-scrape isn't always convenient
            # just to pick up a title-text fix.
            title = html.unescape((row.get("title") or "").strip())
            author = html.unescape((row.get("creators") or "").strip())
            ean_isbn13 = (row.get("ean_isbn13") or "").strip()
            upc_isbn10 = (row.get("upc_isbn10") or "").strip()
            isbn = ean_isbn13 or upc_isbn10 or None
            cover = row.get("notes") or ""
            books.append((title, author, isbn, cover))
    return books


def _gather_scraped_books(
    providers: list[str],
    scrape: bool,
    existing_paths: dict[str, Optional[str]],
    output_dir: str,
) -> dict[str, list[ScrapedBook]]:
    scraped: dict[str, list[ScrapedBook]] = {}

    for provider in providers:
        path = existing_paths.get(provider)

        if scrape and path:
            log.warning(
                "Both --scrape and --%s PATH were given — using the supplied "
                "file, not re-scraping %s.",
                provider,
                provider,
            )

        if not path and scrape:
            module_name = _SCRAPER_MODULES.get(provider)
            if module_name is None:
                log.warning(
                    "No scraper available yet for '%s' — skipping (see docs/backlog.md).",
                    provider,
                )
                continue

            module = importlib.import_module(module_name)
            log.info("Scraping %s…", provider)
            # no_enrich=True here is deliberate: enriching every scraped book
            # up front is wasteful when only the gap subset (books missing
            # from Libib) ultimately needs it — enrich_gap_books() below does
            # that enrichment once reconciliation has narrowed the set down.
            # It also keeps `notes` a clean, unprefixed cover URL, since
            # _load_scrape_csv() below can't reliably distinguish a raw cover
            # URL from an already-series-prefixed notes field.
            result = module.run(output_dir=output_dir, no_enrich=True)
            path = result.csv_path

        if not path:
            continue

        scraped[provider] = _load_scrape_csv(path)
        log.info("Loaded %d book(s) from %s.", len(scraped[provider]), provider)

    return scraped


def run(
    *,
    libib_path: str,
    output_dir: str = ".",
    providers: Optional[list[str]] = None,
    scrape: bool = False,
    chirp: Optional[str] = None,
    kindle: Optional[str] = None,
    kobo: Optional[str] = None,
    nook: Optional[str] = None,
    google: Optional[str] = None,
    dry_run: bool = False,
    no_enrich: bool = False,
    wait_for_rate_limits: bool = False,
    cancel_fn: Callable[[], bool] = lambda: False,
) -> ReconcileRunResult:
    """Read the Libib export, reconcile against the selected provider scrapes,
    and write the gap CSV + reports. Callable directly (CLI, or a future GUI)
    without going through argparse — `main()` below is a thin wrapper around
    this for the CLI entry point.

    `--dry-run` suppresses only this function's own output files (summary,
    gap CSV, orphan/low-confidence/ambiguous reports) — matching the "dry"
    convention already used by every scraper. If `scrape=True`, each provider
    scrape still runs for real and writes its own intermediate CSV to
    `output_dir`, since that CSV is the data source reconciliation needs;
    there's no way to gather scrape data without it under the current
    scraper `run()` contract.

    `wait_for_rate_limits` is passed straight through to `enrich_missing_isbns()`
    and `enrich_gap_books()` — opts into waiting out an Open Library/Google
    Books cooldown instead of skipping that source for books hit during it.

    `cancel_fn` is checked inside `enrich_missing_isbns()` and
    `enrich_gap_books()` — the two stages that make real per-book network
    calls, same cooperative-cancel pattern as every scraper's own
    `resolve_isbns()`/`enrich_books()`.
    """
    providers = providers if providers is not None else list(_PROVIDERS)
    existing_paths: dict[str, Optional[str]] = {
        "chirp": chirp,
        "kindle": kindle,
        "kobo": kobo,
        "nook": nook,
        "google": google,
    }

    if not scrape and not any(existing_paths.get(p) for p in providers):
        raise ValueError(
            "No provider source supplied — pass --scrape or at least one of "
            "--chirp/--kindle/--kobo/--nook/--google PATH."
        )

    log.info("Reading Libib export: %s", libib_path)
    libib_entries = read_libib_export(libib_path)
    log.info("Loaded %d Libib entry/entries.", len(libib_entries))

    scraped_books = _gather_scraped_books(providers, scrape, existing_paths, output_dir)

    log.info(
        "Reconciling %d Libib entry/entries against %d provider scrape(s)…",
        len(libib_entries),
        len(scraped_books),
    )
    result = reconcile(libib_entries, scraped_books)
    result = enrich_missing_isbns(
        result, cancel_fn=cancel_fn, wait_for_rate_limits=wait_for_rate_limits
    )

    gap_books = [r for r in result.scraped_results if r.status == "missing_from_libib"]
    log.info("Reconciliation complete: %d gap book(s) found.", len(gap_books))

    if dry_run:
        log.info("--dry-run set — no reconciliation output files written.")
        return ReconcileRunResult(
            gap_csv_path=None,
            summary_path=None,
            orphan_path=None,
            low_confidence_path=None,
            ambiguous_path=None,
            total_libib_entries=len(libib_entries),
            total_gap_books=len(gap_books),
        )

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")

    summary_path = write_reconciliation_report(result, output_dir, timestamp)
    log.info("Summary written: %s", summary_path)

    enriched_gap_books = enrich_gap_books(
        gap_books,
        no_enrich=no_enrich,
        cancel_fn=cancel_fn,
        wait_for_rate_limits=wait_for_rate_limits,
    )
    gap_csv_path = write_gap_csv(enriched_gap_books, output_dir, timestamp)
    log.info("Gap CSV written: %s", gap_csv_path)

    orphan_path = write_orphan_report(result, output_dir, timestamp)
    if orphan_path:
        log.info("Orphan report written: %s", orphan_path)

    low_confidence_path = write_low_confidence_report(result, output_dir, timestamp)
    if low_confidence_path:
        log.info("Low-confidence match report written: %s", low_confidence_path)

    ambiguous_path = write_ambiguous_report(result, output_dir, timestamp)
    if ambiguous_path:
        log.info("Ambiguous report written: %s", ambiguous_path)

    review_source_paths = {"libib": libib_path, **existing_paths}
    review_snapshot_path = write_review_snapshot(
        result, enriched_gap_books, review_source_paths, output_dir, timestamp
    )
    log.info("Review snapshot written: %s", review_snapshot_path)

    return ReconcileRunResult(
        gap_csv_path=gap_csv_path,
        summary_path=summary_path,
        orphan_path=orphan_path,
        low_confidence_path=low_confidence_path,
        ambiguous_path=ambiguous_path,
        total_libib_entries=len(libib_entries),
        total_gap_books=len(gap_books),
        review_snapshot_path=review_snapshot_path,
    )


def main() -> None:
    args = parse_args()

    try:
        result = run(
            libib_path=args.libib,
            output_dir=args.output_dir,
            providers=args.providers,
            scrape=args.scrape,
            chirp=args.chirp,
            kindle=args.kindle,
            kobo=args.kobo,
            nook=args.nook,
            google=args.google,
            dry_run=args.dry_run,
            no_enrich=args.no_enrich,
            wait_for_rate_limits=args.wait_for_rate_limits,
        )
    except ValueError as exc:
        raise SystemExit(str(exc))

    if result.gap_csv_path:
        print(f"\nUpload '{result.gap_csv_path}' to Libib to fill the gap.")
    elif result.summary_path:
        print(
            f"\n{result.total_gap_books} gap book(s) found. "
            f"See '{result.summary_path}' for details."
        )
    else:
        print(
            f"\n--dry-run: {result.total_gap_books} gap book(s) found. No files written."
        )
