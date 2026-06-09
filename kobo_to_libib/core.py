from __future__ import annotations

import argparse
import csv
import logging
import os
import time
from collections.abc import Iterable
from datetime import datetime
from typing import Optional

from lib import (
    LIBIB_HEADERS,
    classify_identifier,
    dedupe_books_by_title,
    filter_invalid_books,
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
PAGE_WAIT_TIMEOUT: int = 30  # seconds
LIBIB_TYPE = "kobo,ebook"

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


def _login(driver: webdriver.Chrome) -> None:
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


def _parse_items(items: Iterable[WebElement]) -> list[tuple[str, str, str]]:
    """Extract (title, author, cover_url) from a list of li.item-wrapper.book elements."""
    books = []
    for item in items:
        try:
            # ---- Title ----
            title = ""
            try:
                title_el = item.find_element(By.CSS_SELECTOR, _SEL_TITLE)
                title = title_el.text.strip()
            except Exception:
                pass

            # ---- Author ----
            # p.authors.product-field may contain multiple contributor links;
            # we take the first (primary author).
            author = ""
            try:
                author_el = item.find_element(By.CSS_SELECTOR, _SEL_AUTHOR)
                author = author_el.text.strip()
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


def scrape_kobo(max_pages: Optional[int]) -> list[tuple[str, str, str]]:
    """Scrape all books from the Kobo library using a manual-login flow."""
    driver = _build_driver()
    try:
        _login(driver)

        # After login the user is already on the library page; capture the
        # locale-aware URL before navigating anywhere else.
        library_url = _library_url(driver)
        log.info("Library URL detected: %s", library_url)

        books: list[tuple[str, str, str]] = []
        page_number = 1

        while True:
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
) -> list[tuple[str, str, Optional[str], str]]:
    total = len(books)
    records = []

    for idx, (title, author, cover) in enumerate(books, start=1):
        isbn = get_isbn(title, author)
        sleep_between_requests()

        records.append((title, author, isbn, cover))

        if idx % ISBN_LOG_INTERVAL == 0 or idx == total:
            resolved = sum(1 for _, _, i, _ in records if i)
            log.info(
                "ISBN progress: %d/%d looked up, %d resolved.", idx, total, resolved
            )

    return records


# ==========================
# OUTPUT
# ==========================


def write_csv(
    records: list[tuple[str, str, Optional[str], str]], output_dir: str
) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    path = _output_path(output_dir, f"kobo_to_libib_{timestamp}.csv")

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=LIBIB_HEADERS)
        writer.writeheader()
        for title, author, isbn, cover in records:
            upc_isbn10, ean_isbn13 = classify_identifier(isbn) if isbn else ("", "")
            row = {h: "" for h in LIBIB_HEADERS}
            row["title"] = title
            row["creators"] = author
            row["upc_isbn10"] = upc_isbn10
            row["ean_isbn13"] = ean_isbn13
            row["tags"] = LIBIB_TYPE
            row["notes"] = cover
            writer.writerow(row)

    return path


def write_unresolved(
    records: list[tuple[str, str, Optional[str], str]], output_dir: str
) -> Optional[str]:
    unresolved = [(t, a) for t, a, isbn, _ in records if not isbn]
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


# ==========================
# MAIN PIPELINE
# ==========================


def main() -> None:
    args = parse_args()

    if args.pages is not None and args.pages < 1:
        raise SystemExit("--pages must be 1 or greater.")

    log.info("Starting Kobo library scrape…")
    books = scrape_kobo(max_pages=args.pages)

    books = filter_invalid_books(books)

    if not books:
        log.error("No books were scraped. Exiting.")
        return

    log.info("Found %d book(s). Deduplicating…", len(books))
    books = dedupe_books_by_title(books)

    log.info("Found %d book(s). Resolving ISBNs via Open Library…", len(books))
    records = resolve_isbns(books)

    resolved = sum(1 for _, _, isbn, _ in records if isbn)
    unresolved_count = len(records) - resolved

    log.info(
        "ISBN resolution complete: %d/%d resolved, %d unresolved.",
        resolved,
        len(records),
        unresolved_count,
    )

    if args.dry_run:
        log.info("--dry-run set — no output files written.")
        return

    csv_path = write_csv(records, args.output_dir)
    log.info("CSV written: %s", csv_path)

    unresolved_path = write_unresolved(records, args.output_dir)
    if unresolved_path:
        log.info("Unresolved titles written to: %s", unresolved_path)

    print(f"\nUpload '{csv_path}' to Libib to update your collection.")


if __name__ == "__main__":
    main()
