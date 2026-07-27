import csv
import os
import tempfile
from unittest.mock import ANY, patch

from lib import LIBIB_HEADERS, EnrichmentResult

from libib_reconcile.libib_reader import LibibEntry
from libib_reconcile.output import (
    enrich_gap_books,
    write_ambiguous_report,
    write_gap_csv,
    write_low_confidence_report,
    write_orphan_report,
    write_reconciliation_report,
    write_tag_suggestions_report,
)
from libib_reconcile.reconciler import MatchResult, ReconcileResult, ScrapedBookResult

EMPTY = EnrichmentResult()


def _entry(title, creators="", providers=None):
    return LibibEntry(
        title=title,
        creators=creators,
        tags=set(),
        providers=providers or set(),
        ean_isbn13="",
        upc_isbn10="",
        skip=False,
        ambiguous=False,
    )


# ==========================
# enrich_gap_books
# ==========================


def test_enrich_gap_books_no_enrich_skips_lookup():
    gap = [
        ScrapedBookResult(
            "kindle", ("Title", "Author", None, "cover"), "missing_from_libib"
        )
    ]
    enriched = enrich_gap_books(gap, no_enrich=True)
    assert enriched == [(gap[0], EMPTY)]


@patch("libib_reconcile.output.sleep_between_requests")
@patch("libib_reconcile.output.enrich_book")
def test_enrich_gap_books_calls_enrich_book(mock_enrich_book, mock_sleep):
    mock_enrich_book.return_value = EnrichmentResult(description="A description")
    gap = [
        ScrapedBookResult(
            "kindle", ("Title", "Author", None, "cover"), "missing_from_libib"
        )
    ]

    enriched = enrich_gap_books(gap, no_enrich=False)

    assert enriched[0][1].description == "A description"
    mock_enrich_book.assert_called_once_with(
        "Title",
        "Author",
        None,
        None,
        "cover",
        cancel_fn=ANY,
        wait_for_rate_limits=False,
    )
    mock_sleep.assert_called_once()


@patch("libib_reconcile.output.sleep_between_requests")
@patch("libib_reconcile.output.enrich_book")
def test_enrich_gap_books_threads_wait_for_rate_limits(mock_enrich_book, mock_sleep):
    mock_enrich_book.return_value = EMPTY
    gap = [
        ScrapedBookResult(
            "kindle", ("Title", "Author", None, "cover"), "missing_from_libib"
        )
    ]

    enrich_gap_books(gap, no_enrich=False, wait_for_rate_limits=True)

    mock_enrich_book.assert_called_once_with(
        "Title", "Author", None, None, "cover", cancel_fn=ANY, wait_for_rate_limits=True
    )


# ==========================
# write_gap_csv
# ==========================


def test_write_gap_csv_headers():
    gap = [
        (
            ScrapedBookResult(
                "kindle", ("Title", "Author", None, "cover"), "missing_from_libib"
            ),
            EMPTY,
        )
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_gap_csv(gap, tmp, "2026-07-22_12-00")
        with open(path, newline="", encoding="utf-8-sig") as f:
            headers = next(csv.reader(f))
    assert headers == LIBIB_HEADERS
    assert os.path.basename(path) == "reconcile_2026-07-22_12-00_gap.csv"


def test_write_gap_csv_mapping_and_provider_tag():
    gap = [
        (
            ScrapedBookResult(
                "kindle",
                ("Dune", "Frank Herbert", "9780593135204", "cover-url"),
                "missing_from_libib",
            ),
            EMPTY,
        )
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_gap_csv(gap, tmp, "2026-07-22_12-00")
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

    assert rows[0]["title"] == "Dune"
    assert rows[0]["creators"] == "Frank Herbert"
    assert rows[0]["ean_isbn13"] == "9780593135204"
    assert rows[0]["upc_isbn10"] == ""
    assert rows[0]["tags"] == "kindle,ebook"
    assert rows[0]["notes"] == "cover-url"


def test_write_gap_csv_chirp_tag_is_audiobook():
    gap = [
        (
            ScrapedBookResult(
                "chirp", ("Title", "Author", None, "cover"), "missing_from_libib"
            ),
            EMPTY,
        )
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_gap_csv(gap, tmp, "2026-07-22_12-00")
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    assert rows[0]["tags"] == "chirp,audiobook"


def test_write_gap_csv_missing_isbn_falls_back_to_enrichment():
    enrichment = EnrichmentResult(isbn13="9781234567897")
    gap = [
        (
            ScrapedBookResult(
                "kindle", ("Title", "Author", None, "cover"), "missing_from_libib"
            ),
            enrichment,
        )
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_gap_csv(gap, tmp, "2026-07-22_12-00")
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    assert rows[0]["ean_isbn13"] == "9781234567897"


def test_write_gap_csv_enrichment_mapping():
    enrichment = EnrichmentResult(
        description="Desert planet epic.",
        publisher="Ace",
        publish_date="1965",
        length_of="412",
        series_name="Dune Chronicles",
        series_position=1,
    )
    gap = [
        (
            ScrapedBookResult(
                "kindle",
                ("Dune", "Frank Herbert", "9780593135204", "cover-url"),
                "missing_from_libib",
            ),
            enrichment,
        )
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_gap_csv(gap, tmp, "2026-07-22_12-00")
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

    assert rows[0]["description"] == "Desert planet epic."
    assert rows[0]["group"] == "Dune Chronicles"
    assert (
        rows[0]["notes"]
        == "Series: Dune Chronicles #001 || Additional Notes: cover-url"
    )


def test_write_gap_csv_empty_list_writes_header_only():
    with tempfile.TemporaryDirectory() as tmp:
        path = write_gap_csv([], tmp, "2026-07-22_12-00")
        assert os.path.exists(path)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert rows == []


# ==========================
# write_reconciliation_report
# ==========================


def test_write_reconciliation_report_counts():
    libib_results = [
        MatchResult(_entry("Matched"), "kindle", None, "high", "exact_isbn", "matched"),
        MatchResult(_entry("Orphan"), None, None, None, None, "libib_only"),
        MatchResult(_entry("Ambiguous"), None, None, None, None, "ambiguous"),
        MatchResult(_entry("Skipped"), None, None, None, None, "out_of_scope"),
    ]
    scraped_results = [
        ScrapedBookResult("kindle", ("Matched", "Author", "isbn", "cover"), "matched"),
        ScrapedBookResult(
            "kobo", ("Missing", "Author", None, "cover"), "missing_from_libib"
        ),
    ]
    result = ReconcileResult(
        libib_results=libib_results, scraped_results=scraped_results
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = write_reconciliation_report(result, tmp, "2026-07-22_12-00")
        with open(path, encoding="utf-8") as f:
            text = f.read()

    assert "Matched:       1" in text
    assert "Orphans:       1" in text
    assert "Ambiguous:     1" in text
    assert "Out of scope:  1" in text
    assert "kindle: 1 matched, 0 missing" in text
    assert "kobo: 0 matched, 1 missing" in text


def test_write_reconciliation_report_no_providers():
    result = ReconcileResult(libib_results=[], scraped_results=[])
    with tempfile.TemporaryDirectory() as tmp:
        path = write_reconciliation_report(result, tmp, "2026-07-22_12-00")
        with open(path, encoding="utf-8") as f:
            text = f.read()
    assert "(no provider scrapes supplied)" in text


# ==========================
# write_orphan_report
# ==========================


def test_write_orphan_report_returns_none_when_no_orphans():
    result = ReconcileResult(
        libib_results=[
            MatchResult(
                _entry("Matched"), "kindle", None, "high", "exact_isbn", "matched"
            )
        ],
        scraped_results=[],
    )
    with tempfile.TemporaryDirectory() as tmp:
        assert write_orphan_report(result, tmp, "2026-07-22_12-00") is None


def test_write_orphan_report_content():
    entry = _entry("Orphan Book", "Orphan Author", providers={"kobo"})
    result = ReconcileResult(
        libib_results=[MatchResult(entry, None, None, None, None, "libib_only")],
        scraped_results=[],
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = write_orphan_report(result, tmp, "2026-07-22_12-00")
        with open(path, encoding="utf-8") as f:
            text = f.read()
    assert "Orphan Book" in text
    assert "Orphan Author" in text
    assert "kobo" in text


# ==========================
# write_low_confidence_report
# ==========================


def test_write_low_confidence_report_excludes_high_confidence():
    result = ReconcileResult(
        libib_results=[
            MatchResult(
                _entry("Exact"), "kindle", None, "high", "exact_isbn", "matched"
            )
        ],
        scraped_results=[],
    )
    with tempfile.TemporaryDirectory() as tmp:
        assert write_low_confidence_report(result, tmp, "2026-07-22_12-00") is None


def test_write_low_confidence_report_includes_medium_and_low():
    entry_medium = _entry("Medium Match", "Author A")
    entry_low = _entry("Low Match", "Author B")
    book = ("Scraped Title", "Scraped Author", None, "cover")
    result = ReconcileResult(
        libib_results=[
            MatchResult(
                entry_medium, "kobo", book, "medium", "fuzzy_title_author", "matched"
            ),
            MatchResult(entry_low, "kobo", book, "low", "title_only", "matched"),
        ],
        scraped_results=[],
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = write_low_confidence_report(result, tmp, "2026-07-22_12-00")
        with open(path, encoding="utf-8") as f:
            text = f.read()
    assert "Medium Match" in text
    assert "Low Match" in text
    assert "Scraped Title" in text


# ==========================
# write_ambiguous_report
# ==========================


def test_write_ambiguous_report_returns_none_when_none():
    result = ReconcileResult(libib_results=[], scraped_results=[])
    with tempfile.TemporaryDirectory() as tmp:
        assert write_ambiguous_report(result, tmp, "2026-07-22_12-00") is None


def test_write_ambiguous_report_content():
    entry = _entry("Ambiguous Book", "Ambiguous Author")
    result = ReconcileResult(
        libib_results=[MatchResult(entry, None, None, None, None, "ambiguous")],
        scraped_results=[],
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = write_ambiguous_report(result, tmp, "2026-07-22_12-00")
        with open(path, encoding="utf-8") as f:
            text = f.read()
    assert "Ambiguous Book" in text


# ==========================
# write_tag_suggestions_report
# ==========================


def test_write_tag_suggestions_report_returns_none_when_empty():
    with tempfile.TemporaryDirectory() as tmp:
        assert write_tag_suggestions_report([], tmp, "2026-07-22_12-00") is None


def test_write_tag_suggestions_report_content():
    suggestions = [("nook", "Iron Widow", "Xiran Jay Zhao")]
    with tempfile.TemporaryDirectory() as tmp:
        path = write_tag_suggestions_report(suggestions, tmp, "2026-07-22_12-00")
        assert path is not None
        with open(path, encoding="utf-8") as f:
            text = f.read()
    assert "nook" in text
    assert "Iron Widow" in text
    assert "Xiran Jay Zhao" in text
