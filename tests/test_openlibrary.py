from unittest.mock import patch

import pytest

import lib.openlibrary as openlibrary_module
from lib.openlibrary import (
    _normalize_isbn,
    _valid_isbn10,
    _valid_isbn13,
    _best_isbn,
    _title_is_plausible,
    _ol_in_cooldown,
    _ol_query,
    _trip_ol_circuit_breaker,
    dedupe_books_by_title,
    filter_invalid_books,
    get_isbn,
)


@pytest.fixture(autouse=True)
def _reset_ol_circuit_breaker():
    """Module-level state shared across the whole process — reset it around
    every test so one test tripping the breaker can't leak into the next."""
    openlibrary_module._ol_blocked_until = 0.0
    yield
    openlibrary_module._ol_blocked_until = 0.0


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


# -----------------------------
# dedupe_books_by_title
# -----------------------------


def test_dedupe_by_title_merges_when_one_author_is_blank():
    """Recovers from the same book being scraped twice where one copy's
    author extraction glitched to blank, preferring the entry that has an
    author."""
    books = [
        ("Title", "", "cover1"),
        ("Title", "Real Author", "cover2"),
    ]
    result = dedupe_books_by_title(books)
    assert result == [("Title", "Real Author", "cover2")]


def test_dedupe_by_title_three_way_same_title_different_authors():
    """A third book sharing the same title as two already-distinct entries
    compares against all of them, not just the first — and is kept separate
    from both since its author matches neither."""
    books = [
        ("Title", "Author A", "coverA"),
        ("Title", "Author B", "coverB"),
        ("Title", "Author C", "coverC"),
    ]
    result = dedupe_books_by_title(books)
    assert len(result) == 3


def test_dedupe_by_title_three_way_matches_correct_existing_entry():
    """A third entry with a blank author should merge into whichever
    existing same-title entry it can (here, the first one, arbitrarily,
    since blank matches any) rather than being rejected as distinct."""
    books = [
        ("Title", "Author A", "coverA"),
        ("Title", "Author B", "coverB"),
        ("Title", "", "coverC"),
    ]
    result = dedupe_books_by_title(books)
    assert len(result) == 2
    authors = {author for _, author, _ in result}
    assert authors == {"Author A", "Author B"}


def test_dedupe_by_title_records_dropped_entries_for_human_review():
    """Every entry actually removed (not merely replaced) is recorded in the
    optional dropped list, so a caller can write a reviewable report instead
    of relying on scrollback logs."""
    books = [
        ("Apex", "Seth Ring", "coverA"),
        ("Apex", "Seth Ring", "coverB"),  # true duplicate — dropped
        ("Apex", "Mercedes Lackey", "coverC"),  # different book — kept
    ]
    dropped: list[tuple[str, str, str]] = []
    result = dedupe_books_by_title(books, dropped=dropped)

    assert len(result) == 2
    assert len(dropped) == 1
    title, author, reason = dropped[0]
    assert (title, author) == ("Apex", "Seth Ring")
    assert "duplicate" in reason.lower()


# -----------------------------
# filter_invalid_books
# -----------------------------


# -----------------------------
# Open Library circuit breaker
# -----------------------------


@patch("lib.http_retry.requests.get")
def test_ol_query_trips_circuit_breaker_after_exhausting_retries(mock_get):
    mock_get.return_value.raise_for_status.side_effect = Exception("503 error")
    mock_get.return_value.status_code = 503
    mock_get.return_value.headers = {}

    with patch("lib.http_retry.time.sleep"):
        assert not _ol_in_cooldown()
        docs = _ol_query({"title": "Test"}, "Test")
        assert docs == []
        assert _ol_in_cooldown()


@patch("lib.http_retry.requests.get")
def test_ol_query_skips_call_entirely_while_in_cooldown(mock_get):
    _trip_ol_circuit_breaker()

    docs = _ol_query({"title": "Test"}, "Test")

    assert docs == []
    mock_get.assert_not_called()


@patch("lib.http_retry.requests.get")
def test_ol_query_resumes_after_cooldown_expires(mock_get):
    mock_get.return_value.json.return_value = {"docs": [{"title": "Test"}]}
    mock_get.return_value.raise_for_status = lambda: None

    openlibrary_module._ol_blocked_until = 1.0  # long past

    docs = _ol_query({"title": "Test"}, "Test")

    assert docs == [{"title": "Test"}]
    mock_get.assert_called_once()


def test_filter_invalid_books_records_dropped_entries_for_human_review():
    books = [
        ("Valid Title", "Author", "cover"),
        ("", "Blank Title Author", "cover"),
        ("audiobook", "Placeholder Author", "cover"),
    ]
    dropped: list[tuple[str, str, str]] = []
    result = filter_invalid_books(books, dropped=dropped)

    assert len(result) == 1
    assert len(dropped) == 2
    dropped_authors = {author for _, author, _ in dropped}
    assert dropped_authors == {"Blank Title Author", "Placeholder Author"}
