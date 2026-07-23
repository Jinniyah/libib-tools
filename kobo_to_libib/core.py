from __future__ import annotations

import argparse
import csv
import logging
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from lib import (
    LIBIB_HEADERS,
    EnrichmentResult,
    OperationCancelled,
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
LIBIB_TYPE = "kobo,ebook"

# Used in place of a real EnrichmentResult when --no-enrich is set.
_NULL_ENRICHMENT = EnrichmentResult()


@dataclass
class RunResult:
    csv_path: Optional[str]
    unresolved_path: Optional[str]
    total_books: int
    resolved_count: int
    dropped_path: Optional[str] = None


# Kobo library URL — the locale segment (us/en) varies by account region.
# We navigate to the root login page first, then follow the redirect to the
# user's locale-specific library URL after manual login.
KOBO_LOGIN_URL = "https://authorize.kobo.com/us/en/Signin?returnUrl=https%3a%2f%2fwww.kobo.com%2fus%2fen%2fsignin"
KOBO_LIBRARY_PATH = "/us/en/library/books"

# CSS selectors derived from the actual Kobo library DOM:
#   ul.library-items > li.item-wrapper.book
#     div.item-image > img                        ← cover
#     div.item-info.main-meta.triple
#       h2.title.product-field > a               ← title text
#       p.authors.product-field
#         span.visible-contributors
#           span > a.contributor-name            ← author text
_SEL_BOOK_ITEM = "li.item-wrapper.book"
_SEL_TITLE = "h2.title.product-field a"
_SEL_AUTHOR = "p.authors.product-field a.contributor-name"
_SEL_COVER = "div.item-image img"
_SEL_NEXT = "a.next:not(.disabled), button.next:not([disabled])"

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
        description="Export your Kobo ebook library to a Libib-compatible CSV."
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
# KOBO SCRAPING
# ==========================


def _build_driver() -> webdriver.Chrome:
    options = Options()

    # Suppress automation flags that trigger bot-detection.
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

    # Remove the navigator.webdriver fingerprint.
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        },
    )

    return driver


def _default_wait() -> None:
    """CLI default: print the two-tab instructions and block on Enter. A GUI
    (or a test) supplies a different wait_fn — e.g. one that blocks on a
    threading.Event instead — without _login() needing to know the difference."""
    print(
        "\n[ACTION REQUIRED] The Kobo sign-in page is open in your browser.\n"
        "  Kobo's CAPTCHA blocks login on this tab — follow these steps:\n"
        "\n"
        "  1. Copy the URL from the address bar.\n"
        "  2. Open a NEW TAB manually (Ctrl+T) and paste the URL.\n"
        "  3. Log in with your credentials in that new tab.\n"
        "  4. Navigate to your library (My Books → Books) and wait for\n"
        "     the book grid to fully load.\n"
        "  5. Come back here and press Enter to continue: ",
        end="",
        flush=True,
    )
    input()


def _login(
    driver: webdriver.Chrome, wait_fn: Callable[[], None] = _default_wait
) -> None:
    """Log in to Kobo using a manual two-tab strategy to bypass hCaptcha.

    hCaptcha on authorize.kobo.com fingerprints the first Selenium-opened tab
    and blocks it.  Opening a second tab manually — by copying the URL from
    tab 1 into a new tab — inherits the session context and passes cleanly.

    Flow:
      1. Tab 1  — Selenium opens the sign-in page (fingerprinted/blocked).
      2. Tab 2  — user opens manually by copying the URL; login succeeds here.
      3. User navigates to My Books → Books and presses Enter.
      4. Script switches focus to the last tab and verifies the book grid.
    """
    log.info("Opening Kobo sign-in page…")
    driver.get(KOBO_LOGIN_URL)

    wait_fn()

    # Switch to whichever tab the user last navigated to.
    driver.switch_to.window(driver.window_handles[-1])

    # Confirm we can see at least one book card.
    try:
        WebDriverWait(driver, PAGE_WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, _SEL_BOOK_ITEM))
        )
    except Exception:
        raise RuntimeError(
            "Could not find any book cards on the page. "
            "Make sure your Kobo library (My Books → Books) is fully loaded "
            "before pressing Enter."
        )

    log.info("Login confirmed — book grid is visible.")


def _library_url(driver: webdriver.Chrome) -> str:
    """Derive the locale-aware library URL from the current page URL.

    After manual login the browser will be on a URL like:
        https://www.kobo.com/us/en/library/books
    We extract the origin + locale prefix and append KOBO_LIBRARY_PATH so
    pagination navigation stays on the right locale.
    """
    current = driver.current_url  # e.g. https://www.kobo.com/us/en/library/books
    # Walk back to the /library segment and keep everything before it.
    if KOBO_LIBRARY_PATH in current:
        base = current[: current.index(KOBO_LIBRARY_PATH)]
    else:
        # Fallback: use whatever origin/locale we can infer.
        parts = current.split("/")
        # https://www.kobo.com / us / en / ...
        base = "/".join(parts[:5]) if len(parts) >= 5 else "https://www.kobo.com"
    return base + KOBO_LIBRARY_PATH


def _extract_cover_url(img_element: WebElement) -> str:
    srcset = img_element.get_attribute("srcset")
    if srcset:
        # srcset entries are "url 1x, url 2x" — take the highest-res (last).
        last_entry = srcset.split(",")[-1].strip()
        return last_entry.split()[0]
    return img_element.get_attribute("src") or ""


def _output_path(directory: str, filename: str) -> str:
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, filename)


def _element_text(el: WebElement) -> str:
    """Prefer textContent over .text: .text is defined to depend on the
    element actually being rendered/visible, so it reads as "" for anything
    still mid-render/transition — confirmed live on Chirp's identical
    pattern as the source of books coming back with a real author but a
    blank title. textContent reads the DOM's actual text regardless of
    render/animation state."""
    content = el.get_attribute("textContent")
    return content.strip() if content else el.text.strip()


def _parse_items(items: Iterable[WebElement]) -> list[tuple[str, str, str]]:
    """Extract (title, author, cover_url) from a list of li.item-wrapper.book elements."""
    books = []
    for item in items:
        try:
            # ---- Title ----
            title = ""
            try:
                title_el = item.find_element(By.CSS_SELECTOR, _SEL_TITLE)
                title = _element_text(title_el)
            except Exception:
                pass

            # ---- Author ----
            # p.authors.product-field may contain multiple contributor links;
            # we take the first (primary author).
            author = ""
            try:
                author_el = item.find_element(By.CSS_SELECTOR, _SEL_AUTHOR)
                author = _element_text(author_el)
            except Exception:
                pass

            # ---- Cover ----
            cover = ""
            try:
                img_el = item.find_element(By.CSS_SELECTOR, _SEL_COVER)
                cover = _extract_cover_url(img_el)
            except Exception:
                pass

            if title or author:
                books.append((title, author, cover))

        except Exception as exc:
            log.debug("Skipping item due to parse error: %s", exc)

    return books


def scrape_kobo(
    max_pages: Optional[int],
    wait_fn: Callable[[], None] = _default_wait,
    cancel_fn: Callable[[], bool] = lambda: False,
) -> list[tuple[str, str, str]]:
    """Scrape all books from the Kobo library using a manual-login flow."""
    driver = _build_driver()
    try:
        _login(driver, wait_fn=wait_fn)

        # After login the user is already on the library page; capture the
        # locale-aware URL before navigating anywhere else.
        library_url = _library_url(driver)
        log.info("Library URL detected: %s", library_url)

        books: list[tuple[str, str, str]] = []
        page_number = 1

        while True:
            if cancel_fn():
                raise OperationCancelled()

            log.info("Scraping page %d…", page_number)

            WebDriverWait(driver, PAGE_WAIT_TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, _SEL_BOOK_ITEM))
            )

            items = driver.find_elements(By.CSS_SELECTOR, _SEL_BOOK_ITEM)
            page_books = _parse_items(items)
            books.extend(page_books)

            log.info(
                "  → %d book(s) on this page; %d total.", len(page_books), len(books)
            )

            if max_pages is not None and page_number >= max_pages:
                log.info("Reached --pages limit (%d) — stopping.", max_pages)
                break

            # Kobo's pagination: look for a next-page link/button.
            next_el = driver.find_elements(By.CSS_SELECTOR, _SEL_NEXT)
            if not next_el:
                log.info("No further pages found.")
                break

            next_el[0].click()
            page_number += 1
            time.sleep(2)

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


def write_csv(
    records: list[tuple[str, str, Optional[str], str, EnrichmentResult]],
    output_dir: str,
) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    path = _output_path(output_dir, f"kobo_to_libib_{timestamp}.csv")

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
    path = _output_path(output_dir, f"kobo_to_libib_unresolved_{timestamp}.txt")

    with open(path, "w", encoding="utf-8") as f:
        f.write("Titles with no ISBN found in Open Library\n")
        f.write("=" * 44 + "\n\n")
        for title, author in unresolved:
            f.write(f"{title}  —  {author}\n")

    return path


def write_dropped_report(
    dropped: list[tuple[str, str, str]],
    output_dir: str,
) -> Optional[str]:
    """Books filter_invalid_books()/dedupe_books_by_title() removed before
    they ever reached ISBN resolution — a durable, human-reviewable record
    of exactly what was dropped and why, since these are silent by nature
    otherwise (a dropped book just isn't in the output CSV)."""
    if not dropped:
        return None

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    path = _output_path(output_dir, f"kobo_to_libib_dropped_{timestamp}.txt")

    with open(path, "w", encoding="utf-8") as f:
        f.write(
            "Books filtered out or deduplicated before ISBN lookup — "
            "review for false positives\n"
        )
        f.write("=" * 70 + "\n\n")
        for title, author, reason in dropped:
            f.write(f"{title!r} by {author!r}\n    {reason}\n\n")

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
    log.info("Starting Kobo library scrape…")
    books = scrape_kobo(max_pages=pages, wait_fn=wait_fn, cancel_fn=cancel_fn)

    dropped: list[tuple[str, str, str]] = []
    books = filter_invalid_books(books, dropped=dropped)

    if not books:
        log.error("No books were scraped. Exiting.")
        return RunResult(
            csv_path=None, unresolved_path=None, total_books=0, resolved_count=0
        )

    log.info("Found %d book(s). Deduplicating…", len(books))
    books = dedupe_books_by_title(books, dropped=dropped)

    log.info("Found %d book(s). Resolving ISBNs via Open Library…", len(books))
    records = resolve_isbns(books, cancel_fn=cancel_fn)

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

    dropped_path = write_dropped_report(dropped, output_dir)
    if dropped_path:
        log.info("Filtered/deduplicated books written to: %s", dropped_path)

    return RunResult(
        csv_path=csv_path,
        unresolved_path=unresolved_path,
        total_books=len(records),
        resolved_count=resolved,
        dropped_path=dropped_path,
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
