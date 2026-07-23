from unittest.mock import patch

import pytest

from lib import OperationCancelled
from chirp_to_libib.core import (
    dedupe_books_by_title,
    enrich_books,
    filter_invalid_books,
    resolve_isbns,
)


def test_dedupe_books_by_title():
    books = [
        ("Title", "Author A", "cover1"),
        ("Title", "Author B", "cover2"),
    ]
    result = dedupe_books_by_title(books)
    assert len(result) == 1


def test_filter_invalid_books():
    books = [
        ("Valid Title", "Author", "cover"),
        ("", "Author", "cover"),
        ("#", "Author", "cover"),
        ("audiobook", "Author", "cover"),
    ]
    result = filter_invalid_books(books)
    assert len(result) == 1
    assert result[0][0] == "Valid Title"


@patch("chirp_to_libib.core.get_isbn", return_value="1234567890")
@patch("chirp_to_libib.core.sleep_between_requests")
def test_resolve_isbns(mock_sleep, mock_isbn):
    books = [("Title", "Author", "cover")]
    result = resolve_isbns(books)
    assert result[0][2] == "1234567890"
    mock_isbn.assert_called_once()


@patch("chirp_to_libib.core.get_isbn", return_value="1234567890")
@patch("chirp_to_libib.core.sleep_between_requests")
def test_resolve_isbns_stops_when_cancelled(mock_sleep, mock_isbn):
    books = [
        ("Title A", "Author A", "cover"),
        ("Title B", "Author B", "cover"),
    ]
    with pytest.raises(OperationCancelled):
        resolve_isbns(books, cancel_fn=lambda: True)
    mock_isbn.assert_not_called()


@patch("chirp_to_libib.core.enrich_book")
@patch("chirp_to_libib.core.sleep_between_requests")
def test_enrich_books_stops_when_cancelled(mock_sleep, mock_enrich_book):
    records = [
        ("Title A", "Author A", "1234567890", "cover"),
        ("Title B", "Author B", "1234567890", "cover"),
    ]
    with pytest.raises(OperationCancelled):
        enrich_books(records, cancel_fn=lambda: True)
    mock_enrich_book.assert_not_called()
