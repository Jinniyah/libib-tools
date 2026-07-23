from unittest.mock import patch

from lib.openlibrary import (
    _normalize_isbn,
    _valid_isbn10,
    _valid_isbn13,
    _best_isbn,
    get_isbn,
)


def test_normalize_isbn():
    assert _normalize_isbn("0-321-14653-0") == "0321146530"


def test_valid_isbn10():
    assert _valid_isbn10("123456789X")


def test_valid_isbn13():
    assert _valid_isbn13("9781402894626")


def test_best_isbn():
    assert _best_isbn(["0321146530"]) == "0321146530"


@patch("lib.http_retry.requests.get")
def test_get_isbn_title_only(mock_get):
    mock_get.return_value.raise_for_status = lambda: None
    mock_get.return_value.json.return_value = {
        "docs": [{"title": "Test", "isbn": ["0321146530"]}]
    }
    isbn = get_isbn("Test", "")
    assert isbn == "0321146530"
