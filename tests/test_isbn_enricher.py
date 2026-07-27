from unittest.mock import ANY, patch

import pytest

from lib import OperationCancelled
from libib_reconcile.isbn_enricher import enrich_missing_isbns
from libib_reconcile.libib_reader import LibibEntry
from libib_reconcile.reconciler import MatchResult, ReconcileResult, ScrapedBookResult


def _match_result(title: str) -> MatchResult:
    entry = LibibEntry(
        title=title,
        creators="",
        tags=set(),
        providers=set(),
        ean_isbn13="",
        upc_isbn10="",
        skip=False,
        ambiguous=False,
    )
    return MatchResult(entry, None, None, None, None, "libib_only")


@patch("libib_reconcile.isbn_enricher.sleep_between_requests")
@patch("libib_reconcile.isbn_enricher.get_isbn", return_value="9780593135204")
def test_resolves_missing_isbn(mock_get_isbn, mock_sleep):
    result = ReconcileResult(
        libib_results=[],
        scraped_results=[
            ScrapedBookResult(
                "kindle",
                ("New Book", "New Author", None, "cover"),
                "missing_from_libib",
            )
        ],
    )

    enriched = enrich_missing_isbns(result)

    assert enriched.scraped_results[0].book[2] == "9780593135204"
    mock_get_isbn.assert_called_once_with(
        "New Book", "New Author", cancel_fn=ANY, wait_for_rate_limits=False
    )
    mock_sleep.assert_called_once()


@patch("libib_reconcile.isbn_enricher.sleep_between_requests")
@patch("libib_reconcile.isbn_enricher.get_isbn", return_value="9780593135204")
def test_threads_wait_for_rate_limits_into_get_isbn(mock_get_isbn, mock_sleep):
    result = ReconcileResult(
        libib_results=[],
        scraped_results=[
            ScrapedBookResult(
                "kindle",
                ("New Book", "New Author", None, "cover"),
                "missing_from_libib",
            )
        ],
    )

    enrich_missing_isbns(result, wait_for_rate_limits=True)

    mock_get_isbn.assert_called_once_with(
        "New Book", "New Author", cancel_fn=ANY, wait_for_rate_limits=True
    )


@patch("libib_reconcile.isbn_enricher.sleep_between_requests")
@patch("libib_reconcile.isbn_enricher.get_isbn")
def test_skips_books_that_already_have_isbn(mock_get_isbn, mock_sleep):
    result = ReconcileResult(
        libib_results=[],
        scraped_results=[
            ScrapedBookResult(
                "nook",
                ("Nook Book", "Nook Author", "9780593813867", "cover"),
                "missing_from_libib",
            )
        ],
    )

    enriched = enrich_missing_isbns(result)

    assert enriched.scraped_results[0].book[2] == "9780593813867"
    mock_get_isbn.assert_not_called()
    mock_sleep.assert_not_called()


@patch("libib_reconcile.isbn_enricher.sleep_between_requests")
@patch("libib_reconcile.isbn_enricher.get_isbn")
def test_ignores_matched_books(mock_get_isbn, mock_sleep):
    result = ReconcileResult(
        libib_results=[],
        scraped_results=[
            ScrapedBookResult(
                "kindle", ("Matched Book", "Author", None, "cover"), "matched"
            )
        ],
    )

    enriched = enrich_missing_isbns(result)

    assert enriched.scraped_results[0].book[2] is None
    mock_get_isbn.assert_not_called()


@patch("libib_reconcile.isbn_enricher.sleep_between_requests")
@patch("libib_reconcile.isbn_enricher.get_isbn", return_value=None)
def test_stays_unresolved_when_lookup_fails(mock_get_isbn, mock_sleep):
    result = ReconcileResult(
        libib_results=[],
        scraped_results=[
            ScrapedBookResult(
                "kindle",
                ("Unknown Book", "Unknown Author", None, "cover"),
                "missing_from_libib",
            )
        ],
    )

    enriched = enrich_missing_isbns(result)

    assert enriched.scraped_results[0].book[2] is None


@patch("libib_reconcile.isbn_enricher.sleep_between_requests")
@patch("libib_reconcile.isbn_enricher.get_isbn")
def test_noop_when_nothing_needs_lookup(mock_get_isbn, mock_sleep):
    result = ReconcileResult(
        libib_results=[_match_result("Orphan")], scraped_results=[]
    )

    enriched = enrich_missing_isbns(result)

    assert enriched is result
    mock_get_isbn.assert_not_called()


@patch("libib_reconcile.isbn_enricher.sleep_between_requests")
@patch("libib_reconcile.isbn_enricher.get_isbn", return_value="9780593135204")
def test_libib_results_pass_through_unchanged(mock_get_isbn, mock_sleep):
    libib_results = [_match_result("Orphan")]
    result = ReconcileResult(
        libib_results=libib_results,
        scraped_results=[
            ScrapedBookResult(
                "kindle", ("New Book", "Author", None, "cover"), "missing_from_libib"
            )
        ],
    )

    enriched = enrich_missing_isbns(result)

    assert enriched.libib_results is libib_results


@patch("libib_reconcile.isbn_enricher.sleep_between_requests")
@patch("libib_reconcile.isbn_enricher.get_isbn")
def test_raises_operation_cancelled_when_cancel_fn_flips(mock_get_isbn, mock_sleep):
    result = ReconcileResult(
        libib_results=[],
        scraped_results=[
            ScrapedBookResult(
                "kindle", ("Book One", "Author", None, "cover"), "missing_from_libib"
            ),
            ScrapedBookResult(
                "kobo", ("Book Two", "Author", None, "cover"), "missing_from_libib"
            ),
        ],
    )

    with pytest.raises(OperationCancelled):
        enrich_missing_isbns(result, cancel_fn=lambda: True)

    mock_get_isbn.assert_not_called()
