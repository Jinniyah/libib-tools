import os
from unittest.mock import patch

import pytest

from lib import EnrichmentResult

from kindle_to_libib.core import (
    RunResult,
    credentials_from_env,
    dedupe_books_by_title,
    filter_invalid_books,
    resolve_isbns,
    run,
)


def test_dedupe_books_by_title():
    books = [
        ("Title", "Author A", "cover1"),
        ("Title", "Author B", "cover2"),
    ]
    result = dedupe_books_by_title(books)
    assert len(result) == 1


_KINDLE_UI_GARBAGE = frozenset(
    {"content", "devices", "preferences", "privacy settings"}
)


def test_filter_invalid_books():
    books = [
        ("Valid Title", "Author", "cover"),
        ("", "Author", "cover"),
        ("#", "Author", "cover"),
        ("ebook", "Author", "cover"),
        ("devices", "Author", "cover"),
    ]
    result = filter_invalid_books(books, extra_garbage=_KINDLE_UI_GARBAGE)
    assert len(result) == 1


@patch("kindle_to_libib.core.get_isbn", return_value="9781402894626")
@patch("kindle_to_libib.core.sleep_between_requests")
def test_resolve_isbns(mock_sleep, mock_isbn):
    books = [("Title", "Author", "cover")]
    result = resolve_isbns(books)
    assert result[0][2] == "9781402894626"
    mock_isbn.assert_called_once()


# ==========================
# credentials_from_env() — the GUI's non-interactive credentials source
# ==========================


def test_credentials_from_env_returns_values_when_set(monkeypatch):
    monkeypatch.setenv("KINDLE_EMAIL", "test@example.com")
    monkeypatch.setenv("KINDLE_PASSWORD", "secret")

    assert credentials_from_env() == ("test@example.com", "secret")


def test_credentials_from_env_raises_with_copy_pasteable_snippet_when_missing(
    monkeypatch,
):
    monkeypatch.delenv("KINDLE_EMAIL", raising=False)
    monkeypatch.delenv("KINDLE_PASSWORD", raising=False)

    with pytest.raises(ValueError) as exc_info:
        credentials_from_env()

    message = str(exc_info.value)
    assert "KINDLE_EMAIL=" in message
    assert "KINDLE_PASSWORD=" in message
    assert "restart" in message.lower()


# ==========================
# run() — callable directly, no argparse (REFACTOR-6)
# ==========================


@patch("kindle_to_libib.core.write_unresolved", return_value=None)
@patch("kindle_to_libib.core.write_csv", return_value="/out/kindle_to_libib_x.csv")
@patch("kindle_to_libib.core.enrich_book", return_value=EnrichmentResult())
@patch("kindle_to_libib.core.sleep_between_requests")
@patch("kindle_to_libib.core.get_isbn", return_value="9781402894626")
@patch("kindle_to_libib.core.scrape_kindle")
@patch("kindle_to_libib.core._prompt_credentials", return_value=("email", "password"))
def test_run_returns_paths_and_counts(
    mock_creds,
    mock_scrape,
    mock_get_isbn,
    mock_sleep,
    mock_enrich_book,
    mock_write_csv,
    mock_write_unresolved,
):
    mock_scrape.return_value = [("Title", "Author", "cover")]

    result = run(output_dir="/out", credentials_fn=mock_creds)

    assert result.csv_path == "/out/kindle_to_libib_x.csv"
    assert result.total_books == 1
    assert result.resolved_count == 1


@patch("kindle_to_libib.core.scrape_kindle", return_value=[])
@patch("kindle_to_libib.core._prompt_credentials", return_value=("email", "password"))
def test_run_no_books_scraped_returns_empty_result(mock_creds, mock_scrape):
    result = run(credentials_fn=mock_creds)
    assert result == RunResult(
        csv_path=None, unresolved_path=None, total_books=0, resolved_count=0
    )


@patch("kindle_to_libib.core.scrape_kindle", return_value=[])
@patch("kindle_to_libib.core._prompt_credentials", return_value=("email", "password"))
def test_run_clears_credentials_from_environment(mock_creds, mock_scrape, monkeypatch):
    monkeypatch.setenv("KINDLE_EMAIL", "test@example.com")
    monkeypatch.setenv("KINDLE_PASSWORD", "secret")

    run(credentials_fn=mock_creds)

    assert "KINDLE_EMAIL" not in os.environ
    assert "KINDLE_PASSWORD" not in os.environ
