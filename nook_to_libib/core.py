# nook_to_libib/core.py

from __future__ import annotations

import argparse
import csv
import logging
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from lib import (
    LIBIB_HEADERS,
    EnrichmentResult,
    classify_identifier,
    dedupe_books_by_title,
    enrich_book,
    filter_invalid_books,
    format_series_notes,
    get_isbn,
    sleep_between_requests,
)
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# ==========================
# CONFIGURATION
# ==========================

ISBN_LOG_INTERVAL: int = 25
ENRICH_LOG_INTERVAL: int = 25
PAGE_WAIT_TIMEOUT: int = 30  # seconds
LIBIB_TYPE = "nook,ebook"

# Used in place of a real EnrichmentResult when --no-enrich is set.
_NULL_ENRICHMENT = EnrichmentResult()


@dataclass
class RunResult:
    csv_path: Optional[str]
    unresolved_path: Optional[str]
    total_books: int
    resolved_count: int


# Nook library URL — this is where the confirmed DOM selectors below were
# captured from (2026-07-21). Note this is a different host than the
# "my-digital-library" URL mentioned in early planning notes.
NOOK_LIBRARY_URL = "https://nook.barnesandnoble.com/my_library/ebook"

# CSS selectors derived from a DevTools capture of the real Nook library DOM:
#   ul > li[data-test="{ISBN-13}"]                      ← one per book, ISBN on data-test
#     div.equator-tile.book.new-product-tile > div.south > div.info-section
#       div.title > a                                   ← data-product-title has full title
#       a[href*="barnesandnoble.com/search?q="]          ← author link, text is author name
#     img[data-bntrack="LinkedImage"]                    ← cover image, src is the URL
_SEL_BOOK_ITEM = "li[data-test]"
_SEL_TITLE = "div.title > a"
_SEL_AUTHOR = "a[href*='barnesandnoble.com/search?q=']"
_SEL_COVER = "img[data-bntrack='LinkedImage']"

# ==========================
# LOGGING
# ==========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ==========================
# CLI
# ==========================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export your Nook ebook library to a Libib-compatible CSV."
    )
    parser.add_argument("--pages", type=int, default=None, metavar="N")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-dir", default=".", metavar="PATH")
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="Skip metadata/series enrichment (faster, no extra HTTP calls).",
    )
    return parser.parse_args()


# ==========================
# NOOK SCRAPING
# ==========================


def _build_driver() -> webdriver.Chrome:
    options = Options()

    # Suppress the automation flags that trigger Akamai bot-detection.
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # Standard stability flags.
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    # Remove the navigator.webdriver property that sites use to fingerprint Selenium.
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        },
    )

    return driver


def _default_wait() -> None:
    """CLI default: print instructions and block on Enter. A GUI (or a test)
    supplies a different wait_fn — e.g. one that blocks on a threading.Event
    instead — without _login() needing to know the difference."""
    print(
        "\n[ACTION REQUIRED] The Nook digital library page is now open in your browser.\n"
        "  1. Log in with your credentials if prompted (and solve any CAPTCHA if shown).\n"
        "  2. Wait until your library grid is fully loaded.\n"
        "  3. Then come back here and press Enter to continue: ",
        end="",
        flush=True,
    )
    input()


def _login(
    driver: webdriver.Chrome, wait_fn: Callable[[], None] = _default_wait
) -> None:
    log.info("Navigating to Nook digital library…")
    driver.get(NOOK_LIBRARY_URL)

    # B&N's Akamai bot-detection blocks automated login attempts.
    # We open the page and let the user log in manually instead.
    wait_fn()

    # Confirm we can see at least one book tile — doubles as login confirmation.
    try:
        WebDriverWait(driver, PAGE_WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, _SEL_BOOK_ITEM))
        )
    except Exception:
        raise RuntimeError(
            "Could not find any book tiles on the page. "
            "Make sure you are logged in and your library is fully loaded "
            "before pressing Enter."
        )

    log.info("Login confirmed — library grid is visible.")


def _output_path(directory: str, filename: str) -> str:
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, filename)


def _parse_items(items: Iterable[WebElement]) -> list[tuple[str, str, str, str]]:
    """Extract (title, author, isbn, cover_url).

    Unlike the other scrapers, Nook's DOM already carries the ISBN on the
    container itself (`data-test`), so no Open Library lookup is needed for
    the common case — see resolve_isbns() below.
    """
    books = []
    for item in items:
        try:
            isbn = item.get_attribute("data-test") or ""

            title = ""
            try:
                title_el = item.find_element(By.CSS_SELECTOR, _SEL_TITLE)
                title = (
                    title_el.get_attribute("data-product-title") or title_el.text
                ).strip()
            except Exception:
                pass

            author = ""
            try:
                author_el = item.find_element(By.CSS_SELECTOR, _SEL_AUTHOR)
                author = author_el.text.strip()
            except Exception:
                pass

            cover = ""
            try:
                img_el = item.find_element(By.CSS_SELECTOR, _SEL_COVER)
                cover = img_el.get_attribute("src") or ""
            except Exception:
                pass

            if title or author:
                books.append((title, author, isbn, cover))

        except Exception as exc:
            log.debug("Skipping item due to parse error: %s", exc)

    return books


def scrape_nook(
    max_pages: Optional[int], wait_fn: Callable[[], None] = _default_wait
) -> list[tuple[str, str, str, str]]:
    driver = _build_driver()
    try:
        _login(driver, wait_fn=wait_fn)

        log.info("Scraping library…")
        WebDriverWait(driver, PAGE_WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, _SEL_BOOK_ITEM))
        )
        items = driver.find_elements(By.CSS_SELECTOR, _SEL_BOOK_ITEM)
        books = _parse_items(items)
        log.info("Found %d book(s).", len(books))

        # Pagination is unconfirmed for Nook — no next-page control has been
        # observed at typical library sizes, so a single page load is scraped.
        # Revisit if a larger library requires pagination/infinite-scroll handling.
        if max_pages is not None:
            log.info(
                "--pages is accepted for CLI consistency, but Nook pagination is "
                "not yet implemented — only the initial page load is scraped."
            )

        return books
    finally:
        driver.quit()


# ==========================
# FILTER & DEDUPE
# ==========================


def _dedupe_and_filter(
    books: list[tuple[str, str, str, str]],
) -> list[tuple[str, str, str, str]]:
    """Filter and dedupe via the shared lib helpers, which are typed for 3-tuples
    of (title, author, cover). Nook already has the ISBN up front, and ISBN is a
    far more reliable key than title for reattaching the cover afterward, so we
    swap it into the "cover" slot for the shared pass and re-attach the real
    cover by ISBN afterward instead of duplicating filter/dedupe logic here.
    """
    cover_by_isbn = {isbn: cover for _, _, isbn, cover in books if isbn}

    projected = [(title, author, isbn) for title, author, isbn, _ in books]
    projected = filter_invalid_books(projected)
    projected = dedupe_books_by_title(projected)

    return [
        (title, author, isbn, cover_by_isbn.get(isbn, ""))
        for title, author, isbn in projected
    ]


# ==========================
# ISBN RESOLUTION
# ==========================


def resolve_isbns(
    books: list[tuple[str, str, str, str]],
) -> list[tuple[str, str, Optional[str], str]]:
    """Nook already supplies ISBNs from the DOM; only fall back to an Open
    Library lookup for the rare book missing one.
    """
    total = len(books)
    missing = [i for i, (_, _, isbn, _) in enumerate(books) if not isbn]

    if missing:
        log.info("Resolving %d book(s) missing an ISBN via Open Library…", len(missing))

    records: list[tuple[str, str, Optional[str], str]] = []
    resolved_count = 0

    for idx, (title, author, isbn, cover) in enumerate(books, start=1):
        resolved_isbn: Optional[str] = isbn or None
        if not resolved_isbn:
            resolved_isbn = get_isbn(title, author)
            sleep_between_requests()

        records.append((title, author, resolved_isbn, cover))
        if resolved_isbn:
            resolved_count += 1

        if missing and (idx % ISBN_LOG_INTERVAL == 0 or idx == total):
            log.info(
                "ISBN progress: %d/%d checked, %d resolved.", idx, total, resolved_count
            )

    return records


# ==========================
# ENRICHMENT
# ==========================


def enrich_books(
    records: list[tuple[str, str, Optional[str], str]],
) -> list[tuple[str, str, Optional[str], str, EnrichmentResult]]:
    total = len(records)
    enriched = []

    for idx, (title, author, isbn, cover) in enumerate(records, start=1):
        upc_isbn10, ean_isbn13 = classify_identifier(isbn) if isbn else ("", "")
        result = enrich_book(
            title, author, ean_isbn13 or None, upc_isbn10 or None, cover
        )
        sleep_between_requests()

        enriched.append((title, author, isbn, cover, result))

        if idx % ENRICH_LOG_INTERVAL == 0 or idx == total:
            log.info("Enrichment progress: %d/%d book(s) processed.", idx, total)

    return enriched


# ==========================
# OUTPUT
# ==========================


def write_csv(
    records: list[tuple[str, str, Optional[str], str, EnrichmentResult]],
    output_dir: str,
) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    path = _output_path(output_dir, f"nook_to_libib_{timestamp}.csv")

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=LIBIB_HEADERS)
        writer.writeheader()
        for title, author, isbn, cover, enrichment in records:
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
            row["tags"] = LIBIB_TYPE
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


def write_unresolved(
    records: list[tuple[str, str, Optional[str], str, EnrichmentResult]],
    output_dir: str,
) -> Optional[str]:
    unresolved = [(t, a) for t, a, isbn, _, _ in records if not isbn]
    if not unresolved:
        return None

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    path = _output_path(output_dir, f"nook_to_libib_unresolved_{timestamp}.txt")

    with open(path, "w", encoding="utf-8") as f:
        f.write("Titles with no ISBN found in Open Library\n")
        f.write("=" * 44 + "\n\n")
        for title, author in unresolved:
            f.write(f"{title}  —  {author}\n")

    return path


# ==========================
# MAIN PIPELINE
# ==========================


def run(
    *,
    pages: Optional[int] = None,
    dry_run: bool = False,
    output_dir: str = ".",
    no_enrich: bool = False,
    wait_fn: Callable[[], None] = _default_wait,
) -> RunResult:
    """Scrape, resolve, enrich, and write — callable directly (CLI, reconciler,
    or a future GUI) without going through argparse. `main()` below is a thin
    wrapper around this for the CLI entry point.
    """
    log.info("Starting Nook library scrape…")
    books = scrape_nook(max_pages=pages, wait_fn=wait_fn)

    if not books:
        log.error("No books were scraped. Exiting.")
        return RunResult(
            csv_path=None, unresolved_path=None, total_books=0, resolved_count=0
        )

    log.info("Found %d book(s). Filtering and deduplicating…", len(books))
    books = _dedupe_and_filter(books)

    log.info("Found %d book(s). Resolving missing ISBNs via Open Library…", len(books))
    records = resolve_isbns(books)

    resolved = sum(1 for _, _, isbn, _ in records if isbn)
    unresolved_count = len(records) - resolved

    log.info(
        "ISBN resolution complete: %d/%d resolved, %d unresolved.",
        resolved,
        len(records),
        unresolved_count,
    )

    if no_enrich:
        log.info("--no-enrich set — skipping metadata/series enrichment.")
        enriched = [
            (t, a, isbn, cover, _NULL_ENRICHMENT) for t, a, isbn, cover in records
        ]
    else:
        log.info(
            "Enriching %d book(s) via Open Library / Google Books / Wikidata…",
            len(records),
        )
        enriched = enrich_books(records)

    if dry_run:
        log.info("--dry-run set — no output files written.")
        return RunResult(
            csv_path=None,
            unresolved_path=None,
            total_books=len(records),
            resolved_count=resolved,
        )

    csv_path = write_csv(enriched, output_dir)
    log.info("CSV written: %s", csv_path)

    unresolved_path = write_unresolved(enriched, output_dir)
    if unresolved_path:
        log.info("Unresolved titles written to: %s", unresolved_path)

    return RunResult(
        csv_path=csv_path,
        unresolved_path=unresolved_path,
        total_books=len(records),
        resolved_count=resolved,
    )


def main() -> None:
    args = parse_args()

    if args.pages is not None and args.pages < 1:
        raise SystemExit("--pages must be 1 or greater.")

    result = run(
        pages=args.pages,
        dry_run=args.dry_run,
        output_dir=args.output_dir,
        no_enrich=args.no_enrich,
    )

    if result.csv_path:
        print(f"\nUpload '{result.csv_path}' to Libib to update your collection.")


if __name__ == "__main__":
    main()
