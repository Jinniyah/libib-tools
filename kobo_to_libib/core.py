import sys
import time

import requests
from bs4 import BeautifulSoup

from models import BookRecord
from openlibrary import enrich_book_metadata


BASE_URL = "https://www.kobo.com/us/en/library/books"


def fetch_page(session, page_number):
    """Fetch a single Kobo library page."""
    url = BASE_URL
    if page_number > 1:
        url = "%s?page=%d" % (BASE_URL, page_number)

    print("Fetching page %d..." % page_number)
    resp = session.get(url)
    resp.raise_for_status()
    return resp.text


def get_max_page(html):
    """Determine the maximum page number from the pagination controls."""
    soup = BeautifulSoup(html, "html.parser")

    # Pagination links at the bottom of the page
    pagination = soup.select("ul.pagination li a")
    if not pagination:
        return 1

    pages = []
    for a in pagination:
        text = a.get_text(strip=True)
        if text.isdigit():
            pages.append(int(text))

    if not pages:
        return 1

    return max(pages)


def parse_books_from_page(html):
    """Parse all books from a single Kobo library page."""
    soup = BeautifulSoup(html, "html.parser")

    # Each book is in a card-like container
    cards = soup.select("div.book-item")
    books = []

    for card in cards:
        # Title
        title_el = card.select_one("h3.title a")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)

        # Author
        author_el = card.select_one("p.author a")
        if author_el:
            author = author_el.get_text(strip=True)
        else:
            author = "Unknown"

        # Cover image
        cover_el = card.select_one("img.book-cover")
        cover_url = None
        if cover_el is not None and cover_el.has_attr("src"):
            cover_url = cover_el["src"]

        books.append(
            BookRecord(
                title=title,
                author=author,
                cover_url=cover_url,
                isbn=None,
                source="kobo",
            )
        )

    return books


def fetch_all_books(session):
    """Fetch and parse all books from the Kobo library."""
    first_html = fetch_page(session, 1)
    max_page = get_max_page(first_html)

    print("Detected %d page(s) in Kobo library." % max_page)

    all_books = []
    all_books.extend(parse_books_from_page(first_html))

    # Fetch remaining pages, if any
    for page in range(2, max_page + 1):
        # Small delay to be polite
        time.sleep(1)
        html = fetch_page(session, page)
        books = parse_books_from_page(html)
        all_books.extend(books)

    return all_books


def main():
    """Entry point for Kobo → Libib export."""
    session = requests.Session()

    # If you need authentication, copy cookies from your browser into this session.
    # Example:
    # session.cookies.set("kobo_session", "YOUR_COOKIE_VALUE_HERE")

    try:
        books = fetch_all_books(session)
    except requests.HTTPError as e:
        print("Error fetching Kobo library: %s" % e)
        sys.exit(1)

    print("Found %d books in Kobo library." % len(books))

    if not books:
        print("No books found. Exiting.")
        return

    print("Enriching metadata with OpenLibrary...")
    enrich_book_metadata(books)

    output_file = "kobo_export.csv"
    print("Exporting %d records to %s..." % (len(books), output_file))
    BookRecord.export_csv(books, output_file)

    print("Done.")


if __name__ == "__main__":
    main()