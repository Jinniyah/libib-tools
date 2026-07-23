from unittest.mock import patch

from lib.openlibrary import (
    _normalize_isbn,
    _valid_isbn10,
    _valid_isbn13,
    _best_isbn,
    _title_is_plausible,
    _ol_query,
    get_isbn,
)


def test_normalize_isbn():
    assert _normalize_isbn("978-1-4028-9462-6") == "9781402894626"


def test_valid_isbn10():
    assert _valid_isbn10("0321146530")


def test_valid_isbn13():
    assert _valid_isbn13("9781402894626")


def test_best_isbn():
    assert _best_isbn(["9781402894626", "0321146530"]) == "9781402894626"


def test_title_is_plausible():
    assert _title_is_plausible("Hobbit", "The Hobbit")


@patch("lib.http_retry.requests.get")
def test_ol_query_success(mock_get):
    mock_get.return_value.json.return_value = {"docs": [{"title": "Test"}]}
    mock_get.return_value.raise_for_status = lambda: None
    docs = _ol_query({"title": "Test"}, "Test")
    assert docs == [{"title": "Test"}]


@patch("lib.http_retry.requests.get")
def test_get_isbn_title_only(mock_get):
    mock_get.return_value.raise_for_status = lambda: None
    mock_get.return_value.json.return_value = {
        "docs": [{"title": "Test", "isbn": ["0321146530"]}]
    }
    isbn = get_isbn("Test", "")
    assert isbn == "0321146530"
