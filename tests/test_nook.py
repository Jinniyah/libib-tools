import csv
import os
import tempfile
from unittest.mock import ANY, MagicMock, patch

from lib import EnrichmentResult, LIBIB_HEADERS

from nook_to_libib.core import (
    RunResult,
    _dedupe_and_filter,
    _parse_items,
    enrich_books,
    parse_args,
    resolve_isbns,
    run,
    write_csv,
    write_unresolved,
)

# Stand-in for a book with no enrichment data — matches --no-enrich output.
EMPTY = EnrichmentResult()


def _make_mock_item(title="", author="", isbn="", cover=""):
    """Build a mock WebElement matching Nook's confirmed DOM shape."""
    item = MagicMock()
    item.get_attribute.side_effect = lambda attr: isbn if attr == "data-test" else None

    title_el = MagicMock()
    title_el.get_attribute.side_effect = lambda attr: (
        title if attr == "data-product-title" else None
    )
    title_el.text = title

    author_el = MagicMock()
    author_el.text = author

    img_el = MagicMock()
    img_el.get_attribute.side_effect = lambda attr: cover if attr == "src" else None

    def find_element_side_effect(by, selector):
        if selector == "div.title > a":
            return title_el
        if "search?q=" in selector:
            return author_el
        if "LinkedImage" in selector:
            return img_el
        raise Exception("not found")

    item.find_element.side_effect = find_element_side_effect
    return item


# ==========================
# PARSE ITEMS
# ==========================


def test_parse_items_basic():
    items = [
        _make_mock_item(
            "Dune",
            "Frank Herbert",
            "9780593813867",
            "http://cover.example.com/dune.jpg",
        )
    ]
    result = _parse_items(items)
    assert result == [
        ("Dune", "Frank Herbert", "9780593813867", "http://cover.example.com/dune.jpg")
    ]


def test_parse_items_uses_data_product_title_not_truncated_text():
    item = _make_mock_item(
        "The Full Untruncated Title", "Author", "9780593813867", "cover"
    )
    result = _parse_items([item])
    assert result[0][0] == "The Full Untruncated Title"


def test_parse_items_skips_empty_title_and_author():
    items = [_make_mock_item("", "", "9780593813867", "")]
    result = _parse_items(items)
    assert result == []


def test_parse_items_handles_parse_error():
    bad_item = MagicMock()
    bad_item.get_attribute.side_effect = Exception("boom")
    result = _parse_items([bad_item])
    assert result == []


# ==========================
# FILTER & DEDUPE
# ==========================


def test_dedupe_and_filter_removes_duplicates_and_reattaches_cover():
    books = [
        ("Dune", "Frank Herbert", "9780593813867", "cover1"),
        ("Dune", "Frank Herbert", "9780593813867", "cover1"),
    ]
    result = _dedupe_and_filter(books)
    assert result == [("Dune", "Frank Herbert", "9780593813867", "cover1")]


def test_dedupe_and_filter_removes_invalid_titles():
    books = [
        ("Good Book", "Author", "9780593813867", "cover"),
        ("", "Author", "9780000000000", "cover2"),
        ("###", "Author", "9780000000001", "cover3"),
    ]
    result = _dedupe_and_filter(books)
    assert len(result) == 1
    assert result[0][0] == "Good Book"


def test_dedupe_and_filter_isbn_keyed_reattachment():
    """Same-title books from different authors dedupe down to one entry (title
    dedup is a lib limitation), but whichever entry survives must keep its own
    cover — looked up by ISBN, not accidentally swapped with the other book's."""
    books = [
        ("Same Title", "Author A", "1111111111111", "coverA"),
        ("Same Title", "Author B", "2222222222222", "coverB"),
    ]
    result = _dedupe_and_filter(books)
    assert len(result) == 1
    _, _, isbn, cover = result[0]
    expected_cover = {"1111111111111": "coverA", "2222222222222": "coverB"}[isbn]
    assert cover == expected_cover


# ==========================
# ISBN RESOLUTION
# ==========================


@patch("nook_to_libib.core.get_isbn", return_value="9781402894626")
@patch("nook_to_libib.core.sleep_between_requests")
def test_resolve_isbns_trusts_scraped_isbn(mock_sleep, mock_get_isbn):
    books = [("Dune", "Frank Herbert", "9780593813867", "cover")]
    result = resolve_isbns(books)
    assert result[0][2] == "9780593813867"
    mock_get_isbn.assert_not_called()
    mock_sleep.assert_not_called()


@patch("nook_to_libib.core.get_isbn", return_value="9781402894626")
@patch("nook_to_libib.core.sleep_between_requests")
def test_resolve_isbns_falls_back_when_missing(mock_sleep, mock_get_isbn):
    books = [("Unknown Book", "Unknown Author", "", "cover")]
    result = resolve_isbns(books)
    assert result[0][2] == "9781402894626"
    mock_get_isbn.assert_called_once_with(
        "Unknown Book", "Unknown Author", cancel_fn=ANY
    )
    mock_sleep.assert_called_once()


@patch("nook_to_libib.core.get_isbn", return_value=None)
@patch("nook_to_libib.core.sleep_between_requests")
def test_resolve_isbns_still_unresolved_after_fallback(mock_sleep, mock_get_isbn):
    books = [("Unknown Book", "Unknown Author", "", "cover")]
    result = resolve_isbns(books)
    assert result[0][2] is None


# ==========================
# ENRICHMENT
# ==========================


@patch("nook_to_libib.core.enrich_book", return_value=EMPTY)
@patch("nook_to_libib.core.sleep_between_requests")
def test_enrich_books_calls_enrich_book_per_record(mock_sleep, mock_enrich_book):
    records = [
        ("Dune", "Frank Herbert", "9780593813867", "cover"),
        ("Title B", "Author B", None, "coverB"),
    ]
    result = enrich_books(records)
    assert len(result) == 2
    assert mock_enrich_book.call_count == 2
    assert result[0][4] == EMPTY


# ==========================
# CSV OUTPUT
# ==========================


def test_write_csv_headers():
    records = [("Dune", "Frank Herbert", "9780593813867", "cover", EMPTY)]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            headers = next(csv.reader(f))
    assert headers == LIBIB_HEADERS


def test_write_csv_mapping():
    records = [
        (
            "Dune",
            "Frank Herbert",
            "9780593813867",
            "http://cover.example.com/dune.jpg",
            EMPTY,
        )
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    assert rows[0]["title"] == "Dune"
    assert rows[0]["creators"] == "Frank Herbert"
    assert rows[0]["ean_isbn13"] == "9780593813867"
    assert rows[0]["upc_isbn10"] == ""
    assert rows[0]["tags"] == "nook,ebook"
    assert rows[0]["notes"] == "http://cover.example.com/dune.jpg"


def test_write_csv_missing_isbn_falls_back_to_enrichment():
    enrichment = EnrichmentResult(isbn13="9781234567897")
    records = [("Title", "Author", None, "cover", enrichment)]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    assert rows[0]["ean_isbn13"] == "9781234567897"


def test_write_csv_enrichment_mapping():
    enrichment = EnrichmentResult(
        description="Desert planet epic.",
        publisher="Ace",
        publish_date="1965",
        length_of="412",
        series_name="Dune Chronicles",
        series_position=1,
    )
    records = [("Dune", "Frank Herbert", "9780593813867", "cover-url", enrichment)]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    assert rows[0]["description"] == "Desert planet epic."
    assert rows[0]["group"] == "Dune Chronicles"
    assert rows[0]["notes"] == (
        "Series: Dune Chronicles #001 || Additional Notes: cover-url"
    )


# ==========================
# UNRESOLVED OUTPUT
# ==========================


def test_write_unresolved_creates_file():
    records = [
        ("Dune", "Frank Herbert", "9780593813867", "cover", EMPTY),
        ("Unknown Book", "Unknown Author", None, "", EMPTY),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_unresolved(records, tmp)
        assert path is not None
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert "Unknown Book" in text
        assert "Dune" not in text


def test_write_unresolved_returns_none_when_all_resolved():
    records = [("Dune", "Frank Herbert", "9780593813867", "cover", EMPTY)]
    with tempfile.TemporaryDirectory() as tmp:
        result = write_unresolved(records, tmp)
    assert result is None


# ==========================
# CLI
# ==========================


def test_cli_pages():
    with patch("sys.argv", ["prog", "--pages", "3"]):
        args = parse_args()
        assert args.pages == 3


def test_cli_dry_run():
    with patch("sys.argv", ["prog", "--dry-run"]):
        args = parse_args()
        assert args.dry_run is True


def test_cli_no_enrich():
    with patch("sys.argv", ["prog", "--no-enrich"]):
        args = parse_args()
        assert args.no_enrich is True


def test_cli_output_dir():
    with patch("sys.argv", ["prog", "--output-dir", "out"]):
        args = parse_args()
        assert args.output_dir == "out"


# ==========================
# run() — callable directly, no argparse (REFACTOR-6)
# ==========================


@patch("nook_to_libib.core.write_unresolved", return_value=None)
@patch("nook_to_libib.core.write_csv", return_value="/out/nook_to_libib_x.csv")
@patch("nook_to_libib.core.enrich_book", return_value=EMPTY)
@patch("nook_to_libib.core.sleep_between_requests")
@patch("nook_to_libib.core.scrape_nook")
def test_run_returns_paths_and_counts(
    mock_scrape, mock_sleep, mock_enrich_book, mock_write_csv, mock_write_unresolved
):
    mock_scrape.return_value = [("Dune", "Frank Herbert", "9780593135204", "cover")]

    result = run(output_dir="/out")

    assert result.csv_path == "/out/nook_to_libib_x.csv"
    assert result.total_books == 1
    assert result.resolved_count == 1


@patch("nook_to_libib.core.scrape_nook", return_value=[])
def test_run_no_books_scraped_returns_empty_result(mock_scrape):
    result = run()
    assert result == RunResult(
        csv_path=None, unresolved_path=None, total_books=0, resolved_count=0
    )


@patch("nook_to_libib.core.scrape_nook", return_value=[])
def test_run_passes_wait_fn_through_to_scrape(mock_scrape):
    def my_wait() -> None:
        pass

    run(wait_fn=my_wait)

    mock_scrape.assert_called_once_with(max_pages=None, wait_fn=my_wait, cancel_fn=ANY)
