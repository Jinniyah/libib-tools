# chirp_to_libib/core.py

from __future__ import annotations

import argparse
import csv
import getpass
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from collections.abc import Callable, Iterable

from lib import (
    LIBIB_HEADERS,
    EnrichmentResult,
    OperationCancelled,
    classify_identifier,
    enrich_book,
    format_series_notes,
    get_isbn,
    sleep_between_requests,
    dedupe_books_by_title,
    filter_invalid_books,
)

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.remote.webelement import WebElement
from webdriver_manager.chrome import ChromeDriverManager

# ==========================
# CONFIGURATION
# ==========================

ISBN_LOG_INTERVAL: int = 25
ENRICH_LOG_INTERVAL: int = 25
PAGE_WAIT_TIMEOUT: int = 30  # seconds
LIBIB_TYPE = "chirp,audiobook"

# Used in place of a real EnrichmentResult when --no-enrich is set.
_NULL_ENRICHMENT = EnrichmentResult()


@dataclass
class RunResult:
    csv_path: Optional[str]
    unresolved_path: Optional[str]
    total_books: int
    resolved_count: int


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
        description="Export your Chirp audiobook library to a Libib-compatible CSV."
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
# CREDENTIALS
# ==========================


def _prompt_credentials() -> tuple[str, str]:
    email = os.environ.get("CHIRP_EMAIL") or input("Enter your Chirp email: ").strip()
    password = os.environ.get("CHIRP_PASSWORD") or getpass.getpass(
        "Enter your Chirp password: "
    )
    if not email or not password:
        raise ValueError("Both email and password are required.")
    return email, password


# ==========================
# CHIRP SCRAPING
# ==========================


def _build_driver() -> webdriver.Chrome:
    options = Options()

    # Suppress the automation flags that trigger bot-detection on sites like Chirp.
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
        "\n[ACTION REQUIRED] The Chirp login page is now open in your browser.\n"
        "  1. Log in with your credentials (and solve any CAPTCHA if shown).\n"
        "  2. Wait until your library or home page is fully loaded.\n"
        "  3. Then come back here and press Enter to continue: ",
        end="",
        flush=True,
    )
    input()


def _login(
    driver: webdriver.Chrome,
    email: str,
    password: str,
    wait_fn: Callable[[], None] = _default_wait,
) -> None:
    log.info("Navigating to Chirp login page…")
    driver.get("https://www.chirpbooks.com/users/sign_in")

    # Chirp's bot-detection blocks automated login attempts.
    # We open the page and let the user log in manually instead.
    wait_fn()

    # Verify we actually landed on an authenticated page.
    try:
        WebDriverWait(driver, PAGE_WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "a[href='/library']"))
        )
    except Exception:
        raise RuntimeError(
            "Could not confirm a successful login. "
            "Make sure you are fully logged in and your library is visible "
            "before pressing Enter."
        )

    log.info("Login confirmed.")


def _extract_cover_url(img_element: WebElement) -> str:
    srcset = img_element.get_attribute("srcset")
    if srcset:
        last_entry = srcset.split(",")[-1].strip()
        return last_entry.split()[0]
    return img_element.get_attribute("src") or ""


def _output_path(directory: str, filename: str) -> str:
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, filename)


def _parse_items(items: Iterable[WebElement]) -> list[tuple[str, str, str]]:
    books = []
    for item in items:
        try:
            title_el = item.find_element(By.CSS_SELECTOR, "a[href^='/audiobooks/']")
            title = title_el.text.strip()

            try:
                byline = item.find_element(
                    By.CSS_SELECTOR, "div[class*='byline']"
                ).text.strip()
            except Exception:
                byline = ""

            author = re.sub(r"(?i)^\s*by\s+", "", byline).strip()

            try:
                img_el = item.find_element(
                    By.CSS_SELECTOR,
                    "img[data-testid='cover-image-image'], img[class*='cover-image-image']",
                )
                cover = _extract_cover_url(img_el)
            except Exception:
                cover = ""

            books.append((title, author, cover))
        except Exception as exc:
            log.debug("Skipping item due to parse error: %s", exc)
    return books


def scrape_chirp(
    email: str,
    password: str,
    max_pages: Optional[int],
    wait_fn: Callable[[], None] = _default_wait,
    cancel_fn: Callable[[], bool] = lambda: False,
) -> list[tuple[str, str, str]]:
    driver = _build_driver()
    try:
        _login(driver, email, password, wait_fn=wait_fn)
        log.info("Navigating to library…")
        driver.get("https://www.chirpbooks.com/library?sort=recently_added")
        time.sleep(2)

        books = []
        page_number = 1

        while True:
            if cancel_fn():
                raise OperationCancelled()

            log.info("Scraping page %d…", page_number)

            WebDriverWait(driver, PAGE_WAIT_TIMEOUT).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//div[.//a[starts-with(@href,'/audiobooks/')]]")
                )
            )

            items = driver.find_elements(
                By.XPATH, "//div[.//a[starts-with(@href,'/audiobooks/')]]"
            )
            page_books = _parse_items(items)
            books.extend(page_books)

            log.info(
                "  → %d book(s) on this page; %d total.", len(page_books), len(books)
            )

            if max_pages is not None and page_number >= max_pages:
                log.info("Reached --pages limit (%d) — stopping.", max_pages)
                break

            next_el = driver.find_elements(
                By.CSS_SELECTOR, "li.rc-pagination-next[aria-disabled='false']"
            )
            if not next_el:
                log.info("No further pages found.")
                break

            next_el[0].click()
            page_number += 1

        return books
    finally:
        driver.quit()


# ==========================
# ISBN RESOLUTION
# ==========================


def resolve_isbns(
    books: list[tuple[str, str, str]],
    cancel_fn: Callable[[], bool] = lambda: False,
) -> list[tuple[str, str, Optional[str], str]]:
    total = len(books)
    records = []

    for idx, (title, author, cover) in enumerate(books, start=1):
        if cancel_fn():
            raise OperationCancelled()

        log.info("Resolving ISBN %d/%d: '%s'…", idx, total, title)
        isbn = get_isbn(title, author, cancel_fn=cancel_fn)
        sleep_between_requests()

        records.append((title, author, isbn, cover))

        if idx % ISBN_LOG_INTERVAL == 0 or idx == total:
            resolved = sum(1 for _, _, i, _ in records if i)
            log.info(
                "ISBN progress: %d/%d looked up, %d resolved.", idx, total, resolved
            )

    return records


# ==========================
# ENRICHMENT
# ==========================


def enrich_books(
    records: list[tuple[str, str, Optional[str], str]],
    cancel_fn: Callable[[], bool] = lambda: False,
) -> list[tuple[str, str, Optional[str], str, EnrichmentResult]]:
    total = len(records)
    enriched = []

    for idx, (title, author, isbn, cover) in enumerate(records, start=1):
        if cancel_fn():
            raise OperationCancelled()

        log.info("Enriching %d/%d: '%s'…", idx, total, title)
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

        enriched.append((title, author, isbn, cover, result))

        if idx % ENRICH_LOG_INTERVAL == 0 or idx == total:
            log.info("Enrichment progress: %d/%d book(s) processed.", idx, total)

    return enriched


# ==========================
# OUTPUT
# ==========================


# Full Libib CSV column order — imported from lib
# LIBIB_HEADERS and classify_identifier are provided by lib.openlibrary


def write_csv(
    records: list[tuple[str, str, Optional[str], str, EnrichmentResult]],
    output_dir: str,
) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    path = _output_path(output_dir, f"chirp_to_libib_{timestamp}.csv")

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
    path = _output_path(output_dir, f"chirp_to_libib_unresolved_{timestamp}.txt")

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
    cancel_fn: Callable[[], bool] = lambda: False,
) -> RunResult:
    """Scrape, resolve, enrich, and write — callable directly (CLI, reconciler,
    or a future GUI) without going through argparse. `main()` below is a thin
    wrapper around this for the CLI entry point.
    """
    # Credentials are no longer used for automated login (Chirp's bot-detection
    # requires manual login). The prompts are kept here in case they are needed
    # in future, but are commented out for now.
    # email, password = _prompt_credentials()

    log.info("Starting Chirp library scrape…")
    books = scrape_chirp("", "", max_pages=pages, wait_fn=wait_fn, cancel_fn=cancel_fn)

    # os.environ.pop("CHIRP_EMAIL", None)
    # os.environ.pop("CHIRP_PASSWORD", None)

    books = filter_invalid_books(books)

    if not books:
        log.error("No books were scraped. Exiting.")
        return RunResult(
            csv_path=None, unresolved_path=None, total_books=0, resolved_count=0
        )

    log.info("Found %d book(s). Deduplicating…", len(books))
    books_deduped = dedupe_books_by_title(books)

    log.info("Found %d book(s). Resolving ISBNs via Open Library…", len(books_deduped))
    records = resolve_isbns(books_deduped, cancel_fn=cancel_fn)

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
        enriched = enrich_books(records, cancel_fn=cancel_fn)

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
