import csv
import os
import tempfile
from unittest.mock import ANY, MagicMock, patch

from kobo_to_libib.core import (
    RunResult,
    _parse_items,
    resolve_isbns,
    run,
    write_csv,
    write_dropped_report,
    write_unresolved,
)
from lib import (
    EnrichmentResult,
    LIBIB_HEADERS,
    dedupe_books_by_title,
    filter_invalid_books,
)

# Stand-in for a book with no enrichment data — matches --no-enrich output.
EMPTY = EnrichmentResult()

# ==========================
# PARSE ITEMS
# ==========================


def _make_mock_item(title="", author="", cover=""):
    """Build a mock WebElement that returns the given title/author/cover."""
    item = MagicMock()

    title_el = MagicMock()
    title_el.get_attribute.return_value = title or None
    title_el.text = title

    author_el = MagicMock()
    author_el.get_attribute.return_value = author or None
    author_el.text = author

    img_el = MagicMock()
    img_el.get_attribute.side_effect = lambda attr: cover if attr == "src" else None

    def find_element_side_effect(by, selector):
        if "title" in selector:
            return title_el
        if "author" in selector or "contributor" in selector:
            return author_el
        if selector == "img":
            return img_el
        raise Exception("not found")

    item.find_element.side_effect = find_element_side_effect
    return item


def test_parse_items_basic():
    items = [
        _make_mock_item("Dune", "Frank Herbert", "http://cover.example.com/dune.jpg")
    ]
    result = _parse_items(items)
    assert len(result) == 1
    assert result[0][0] == "Dune"
    assert result[0][1] == "Frank Herbert"


def test_parse_items_skips_empty():
    items = [_make_mock_item("", "", "")]
    result = _parse_items(items)
    assert result == []


def test_parse_items_handles_parse_error():
    bad_item = MagicMock()
    bad_item.find_element.side_effect = Exception("boom")
    result = _parse_items([bad_item])
    assert result == []


# ==========================
# DEDUP & FILTER
# ==========================


def test_dedupe_books_by_title():
    books = [
        ("The Martian", "Andy Weir", "cover1"),
        ("The Martian", "Andy Weir", "cover2"),
    ]
    result = dedupe_books_by_title(books)
    assert len(result) == 1


def test_filter_invalid_books():
    books = [
        ("Good Book", "Author", "cover"),
        ("", "Author", "cover"),
        ("ebook", "Author", "cover"),
        ("###", "Author", "cover"),
    ]
    result = filter_invalid_books(books)
    assert len(result) == 1
    assert result[0][0] == "Good Book"


# ==========================
# ISBN RESOLUTION
# ==========================


@patch("kobo_to_libib.core.get_isbn", return_value="9781402894626")
@patch("kobo_to_libib.core.sleep_between_requests")
def test_resolve_isbns(mock_sleep, mock_isbn):
    books = [("Dune", "Frank Herbert", "cover")]
    result = resolve_isbns(books)
    assert result[0][2] == "9781402894626"
    mock_isbn.assert_called_once_with("Dune", "Frank Herbert", cancel_fn=ANY)


@patch("kobo_to_libib.core.get_isbn", return_value=None)
@patch("kobo_to_libib.core.sleep_between_requests")
def test_resolve_isbns_unresolved(mock_sleep, mock_isbn):
    books = [("Unknown Book", "Unknown Author", "cover")]
    result = resolve_isbns(books)
    assert result[0][2] is None


# ==========================
# CSV OUTPUT
# ==========================


def test_write_csv_headers():
    records = [("Dune", "Frank Herbert", "9781402894626", "cover", EMPTY)]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            headers = next(csv.reader(f))
    assert headers == LIBIB_HEADERS


def test_write_csv_isbn13_mapping():
    records = [
        (
            "Dune",
            "Frank Herbert",
            "9781402894626",
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
    assert rows[0]["upc_isbn10"] == ""
    assert rows[0]["ean_isbn13"] == "9781402894626"
    assert rows[0]["tags"] == "kobo,ebook"
    assert rows[0]["notes"] == "http://cover.example.com/dune.jpg"


def test_write_csv_isbn10_mapping():
    records = [("Dune", "Frank Herbert", "1402894627", "cover", EMPTY)]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    assert rows[0]["upc_isbn10"] == "1402894627"
    assert rows[0]["ean_isbn13"] == ""


def test_write_csv_no_isbn():
    records = [("No ISBN Book", "Author", None, "", EMPTY)]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    assert rows[0]["upc_isbn10"] == ""
    assert rows[0]["ean_isbn13"] == ""


def test_write_csv_enrichment_mapping():
    enrichment = EnrichmentResult(
        description="Desert planet epic.",
        publisher="Ace",
        publish_date="1965",
        length_of="412",
        series_name="Dune Chronicles",
        series_position=1,
    )
    records = [("Dune", "Frank Herbert", "9781402894626", "cover-url", enrichment)]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    assert rows[0]["description"] == "Desert planet epic."
    assert rows[0]["publisher"] == "Ace"
    assert rows[0]["publish_date"] == "1965"
    assert rows[0]["length_of"] == "412"
    assert rows[0]["group"] == "Dune Chronicles"
    assert rows[0]["notes"] == (
        "Series: Dune Chronicles #001 || Additional Notes: cover-url"
    )


def test_write_csv_empty_non_mapped_columns():
    records = [("Dune", "Frank Herbert", "9781402894626", "cover", EMPTY)]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    non_mapped = [
        "added",
        "began_date",
        "call_numbers",
        "completed_date",
        "copies",
        "description",
        "group",
        "ddc",
        "lcc",
        "lccn",
        "oclc",
        "lexile",
        "length_of",
        "number_of_discs",
        "aspect_ratio",
        "price",
        "publish_date",
        "publisher",
        "rating",
        "review",
        "review_date",
        "status",
    ]
    for col in non_mapped:
        assert rows[0][col] == "", f"Expected empty string for column '{col}'"


# ==========================
# UNRESOLVED OUTPUT
# ==========================


def test_write_unresolved_creates_file():
    records = [
        ("Dune", "Frank Herbert", "9781402894626", "cover", EMPTY),
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


# ==========================
# run() — callable directly, no argparse (REFACTOR-6)
# ==========================


@patch("kobo_to_libib.core.write_unresolved", return_value=None)
@patch("kobo_to_libib.core.write_csv", return_value="/out/kobo_to_libib_x.csv")
@patch("kobo_to_libib.core.enrich_book", return_value=EMPTY)
@patch("kobo_to_libib.core.sleep_between_requests")
@patch("kobo_to_libib.core.get_isbn", return_value="9781402894626")
@patch("kobo_to_libib.core.scrape_kobo")
def test_run_returns_paths_and_counts(
    mock_scrape,
    mock_get_isbn,
    mock_sleep,
    mock_enrich_book,
    mock_write_csv,
    mock_write_unresolved,
):
    mock_scrape.return_value = [("Dune", "Frank Herbert", "cover")]

    result = run(output_dir="/out")

    assert result.csv_path == "/out/kobo_to_libib_x.csv"
    assert result.total_books == 1
    assert result.resolved_count == 1


@patch("kobo_to_libib.core.scrape_kobo", return_value=[])
def test_run_no_books_scraped_returns_empty_result(mock_scrape):
    result = run()
    assert result == RunResult(
        csv_path=None, unresolved_path=None, total_books=0, resolved_count=0
    )


@patch("kobo_to_libib.core.scrape_kobo", return_value=[])
def test_run_passes_wait_fn_through_to_scrape(mock_scrape):
    def my_wait() -> None:
        pass

    run(wait_fn=my_wait)

    mock_scrape.assert_called_once_with(max_pages=None, wait_fn=my_wait, cancel_fn=ANY)


def test_write_unresolved_returns_none_when_all_resolved():
    records = [("Dune", "Frank Herbert", "9781402894626", "cover", EMPTY)]
    with tempfile.TemporaryDirectory() as tmp:
        result = write_unresolved(records, tmp)
    assert result is None


def test_write_dropped_report_creates_file():
    dropped = [
        ("", "Some Author", "Filter: blank, or fewer than 2 alphanumeric characters"),
        (
            "Apex",
            "Mercedes Lackey",
            "Deduplication: duplicate of 'Apex' by 'Seth Ring'",
        ),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_dropped_report(dropped, tmp)
        assert path is not None
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            text = f.read()
    assert "Some Author" in text
    assert "Apex" in text
    assert "Seth Ring" in text


def test_write_dropped_report_returns_none_when_nothing_dropped():
    with tempfile.TemporaryDirectory() as tmp:
        result = write_dropped_report([], tmp)
    assert result is None
