# libib_reconcile/isbn_enricher.py
#
# Resolves ISBNs for gap books (missing_from_libib) that don't already have
# one — e.g. a book whose scraper-side lookup never succeeded, or a provider
# scrape supplied pre-resolution. Books that already have an ISBN (Nook's DOM,
# a future Google API response) are left untouched — no redundant lookup.

from __future__ import annotations

import logging

from lib import get_isbn, sleep_between_requests

from libib_reconcile.reconciler import ReconcileResult, ScrapedBookResult

log = logging.getLogger(__name__)

ISBN_LOG_INTERVAL: int = 25


def enrich_missing_isbns(result: ReconcileResult) -> ReconcileResult:
    """Resolve ISBNs for scraped books classified missing_from_libib that
    don't already have one. `libib_results` pass through unchanged.
    """
    needs_lookup = [
        r
        for r in result.scraped_results
        if r.status == "missing_from_libib" and not r.book[2]
    ]
    total = len(needs_lookup)

    if not total:
        return result

    log.info("Resolving %d gap book(s) missing an ISBN via Open Library…", total)

    checked = 0
    resolved = 0
    enriched: list[ScrapedBookResult] = []

    for r in result.scraped_results:
        if r.status != "missing_from_libib" or r.book[2]:
            enriched.append(r)
            continue

        checked += 1
        title, author, _, cover = r.book
        isbn = get_isbn(title, author)
        sleep_between_requests()
        if isbn:
            resolved += 1

        enriched.append(
            ScrapedBookResult(r.provider, (title, author, isbn, cover), r.status)
        )

        if checked % ISBN_LOG_INTERVAL == 0 or checked == total:
            log.info(
                "ISBN progress: %d/%d checked, %d resolved.", checked, total, resolved
            )

    log.info("ISBN enrichment complete: %d/%d gap book(s) resolved.", resolved, total)

    return ReconcileResult(libib_results=result.libib_results, scraped_results=enriched)
