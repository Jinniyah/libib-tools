import os
from unittest.mock import patch

import pytest

from lib.enricher import (
    EnrichmentResult,
    _fetch_ai_metadata,
    _fetch_google_books_metadata,
    _fetch_open_library,
    _fetch_openai_metadata,
    _fetch_wikidata_series,
    enrich_book,
    format_series_notes,
)


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch("lib.enricher.sleep_between_requests"):
        yield


@pytest.fixture(autouse=True)
def _clean_ai_env(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


# -----------------------------
# format_series_notes
# -----------------------------


def test_format_series_notes_with_position():
    result = format_series_notes("The Dragon Knight", 9, "cover-url")
    assert result == "Series: The Dragon Knight #009 || Additional Notes: cover-url"


def test_format_series_notes_position_1():
    result = format_series_notes("Some Series", 1, "")
    assert result == "Series: Some Series #001 || Additional Notes: "


def test_format_series_notes_position_99():
    result = format_series_notes("Some Series", 99, "notes")
    assert "#099" in result


def test_format_series_notes_position_100():
    result = format_series_notes("Some Series", 100, "notes")
    assert "#100" in result


def test_format_series_notes_unknown_position():
    result = format_series_notes("The Dragon Knight", None, "cover-url")
    assert result == "Series: The Dragon Knight #ZZZ || Additional Notes: cover-url"


def test_format_series_notes_no_series_existing_notes():
    assert format_series_notes(None, None, "original notes") == "original notes"


def test_format_series_notes_no_series_empty_notes():
    assert format_series_notes(None, None, "") == ""


# -----------------------------
# _fetch_open_library
# -----------------------------


@patch("lib.enricher._http_get_json")
def test_fetch_open_library_isbn_hit(mock_get):
    def side_effect(url, context, params=None, headers=None):
        if url.startswith("https://openlibrary.org/isbn/"):
            return {
                "publishers": ["Tor Books"],
                "publish_date": "2020",
                "number_of_pages": 320,
                "isbn_10": ["1234567890"],
                "isbn_13": ["9781234567897"],
                "works": [{"key": "/works/OL123W"}],
            }
        if url.endswith("/works/OL123W.json"):
            return {"description": "A great book."}
        return None

    mock_get.side_effect = side_effect

    result = _fetch_open_library("9781234567897", "Title", "Author")
    assert result["publisher"] == "Tor Books"
    assert result["publish_date"] == "2020"
    assert result["length_of"] == "320"
    assert result["isbn10"] == "1234567890"
    assert result["isbn13"] == "9781234567897"
    assert result["description"] == "A great book."


@patch("lib.enricher._http_get_json")
def test_fetch_open_library_description_dict_value(mock_get):
    def side_effect(url, context, params=None, headers=None):
        if url.startswith("https://openlibrary.org/isbn/"):
            return {"works": [{"key": "/works/OL999W"}]}
        if url.endswith("/works/OL999W.json"):
            return {"description": {"type": "/type/text", "value": "Nested desc."}}
        return None

    mock_get.side_effect = side_effect

    result = _fetch_open_library("9781234567897", "Title", "Author")
    assert result["description"] == "Nested desc."


@patch("lib.enricher._ol_query")
@patch("lib.enricher._http_get_json")
def test_fetch_open_library_title_fallback(mock_get, mock_search):
    mock_get.return_value = None
    mock_search.return_value = [
        {
            "key": "/works/OL5W",
            "title": "Title",
            "publisher": ["Ace"],
            "first_publish_year": 1999,
            "number_of_pages_median": 200,
            "isbn": ["9781234567897"],
        }
    ]

    result = _fetch_open_library(None, "Title", "Author")
    assert result["publisher"] == "Ace"
    assert result["publish_date"] == "1999"
    assert result["length_of"] == "200"
    assert result["isbn13"] == "9781234567897"


@patch("lib.enricher._ol_query", return_value=[])
@patch("lib.enricher._http_get_json", return_value=None)
def test_fetch_open_library_miss(mock_get, mock_search):
    result = _fetch_open_library(None, "Nonexistent Title", "Nobody")
    assert result == {}


# -----------------------------
# _fetch_google_books_metadata
# -----------------------------


@patch("lib.enricher._http_get_json")
def test_fetch_google_books_hit(mock_get):
    mock_get.return_value = {
        "items": [
            {
                "volumeInfo": {
                    "title": "Title",
                    "description": "GB description.",
                    "publisher": "Penguin",
                    "publishedDate": "2018-05-01",
                    "pageCount": 250,
                    "industryIdentifiers": [
                        {"type": "ISBN_13", "identifier": "9781234567897"},
                        {"type": "ISBN_10", "identifier": "1234567890"},
                    ],
                }
            }
        ]
    }

    result = _fetch_google_books_metadata(None, "Title", "Author")
    assert result["description"] == "GB description."
    assert result["publisher"] == "Penguin"
    assert result["publish_date"] == "2018-05-01"
    assert result["length_of"] == "250"
    assert result["isbn13"] == "9781234567897"
    assert result["isbn10"] == "1234567890"


@patch("lib.enricher._http_get_json", return_value=None)
def test_fetch_google_books_miss(mock_get):
    assert _fetch_google_books_metadata(None, "Title", "Author") == {}


@patch("lib.enricher._http_get_json")
def test_fetch_google_books_implausible_title_skipped(mock_get):
    mock_get.return_value = {
        "items": [{"volumeInfo": {"title": "Completely Different Book"}}]
    }
    result = _fetch_google_books_metadata(None, "Title", "Author")
    assert result == {}


# -----------------------------
# enrich_book fallback chain
# -----------------------------


@patch("lib.enricher._fetch_wikidata_series", return_value=(None, None))
@patch("lib.enricher._fetch_google_books_metadata")
@patch("lib.enricher._fetch_open_library")
def test_enrich_book_open_library_full_hit(mock_ol, mock_gb, mock_wd):
    mock_ol.return_value = {
        "description": "OL description",
        "publisher": "OL publisher",
        "publish_date": "2001",
        "length_of": "100",
        "isbn13": "9781234567897",
        "isbn10": "1234567890",
    }

    result = enrich_book("Title", "Author", None, None, "cover")
    assert result.description == "OL description"
    assert result.publisher == "OL publisher"
    assert result.publish_date == "2001"
    assert result.length_of == "100"
    assert result.isbn13 == "9781234567897"
    assert result.isbn10 == "1234567890"
    mock_gb.assert_not_called()


@patch("lib.enricher._fetch_wikidata_series", return_value=(None, None))
@patch("lib.enricher._fetch_google_books_metadata")
@patch("lib.enricher._fetch_open_library", return_value={})
def test_enrich_book_google_books_fallback(mock_ol, mock_gb, mock_wd):
    mock_gb.return_value = {
        "description": "GB description",
        "publisher": "GB publisher",
        "publish_date": "2002",
        "length_of": "150",
    }

    result = enrich_book("Title", "Author", None, None, "cover")
    assert result.description == "GB description"
    assert result.publisher == "GB publisher"
    mock_gb.assert_called_once()


@patch("lib.enricher._fetch_wikidata_series", return_value=(None, None))
@patch("lib.enricher._fetch_google_books_metadata", return_value={})
@patch("lib.enricher._fetch_open_library", return_value={})
def test_enrich_book_both_metadata_sources_miss(mock_ol, mock_gb, mock_wd):
    result = enrich_book("Title", "Author", None, None, "cover")
    assert result == EnrichmentResult(
        isbn13=None,
        isbn10=None,
        description=None,
        publisher=None,
        publish_date=None,
        length_of=None,
        series_name=None,
        series_position=None,
    )


@patch("lib.enricher._fetch_wikidata_series", return_value=(None, None))
@patch("lib.enricher._fetch_google_books_metadata", return_value={})
@patch("lib.enricher._fetch_open_library", return_value={})
def test_enrich_book_preserves_existing_isbn(mock_ol, mock_gb, mock_wd):
    result = enrich_book("Title", "Author", "9781111111111", "1111111111", "cover")
    assert result.isbn13 == "9781111111111"
    assert result.isbn10 == "1111111111"


@patch("lib.enricher._fetch_wikidata_series", return_value=("The Dragon Knight", 9))
@patch("lib.enricher._fetch_google_books_metadata", return_value={})
@patch("lib.enricher._fetch_open_library", return_value={})
def test_enrich_book_series_hit_with_position(mock_ol, mock_gb, mock_wd):
    result = enrich_book("Title", "Author", None, None, "cover")
    assert result.series_name == "The Dragon Knight"
    assert result.series_position == 9


@patch("lib.enricher._fetch_wikidata_series", return_value=("The Dragon Knight", None))
@patch("lib.enricher._fetch_google_books_metadata", return_value={})
@patch("lib.enricher._fetch_open_library", return_value={})
def test_enrich_book_series_hit_without_position(mock_ol, mock_gb, mock_wd):
    result = enrich_book("Title", "Author", None, None, "cover")
    assert result.series_name == "The Dragon Knight"
    assert result.series_position is None


@patch("lib.enricher._fetch_wikidata_series", return_value=(None, None))
@patch("lib.enricher._fetch_google_books_metadata", return_value={})
@patch("lib.enricher._fetch_open_library", return_value={})
def test_enrich_book_series_miss(mock_ol, mock_gb, mock_wd):
    result = enrich_book("Title", "Author", None, None, "cover")
    assert result.series_name is None
    assert result.series_position is None


@patch("lib.enricher._fetch_ai_metadata")
@patch("lib.enricher._fetch_wikidata_series", return_value=(None, None))
@patch("lib.enricher._fetch_google_books_metadata", return_value={})
@patch("lib.enricher._fetch_open_library", return_value={})
def test_enrich_book_ai_fallback_skipped_when_provider_unset(
    mock_ol, mock_gb, mock_wd, mock_ai
):
    enrich_book("Title", "Author", None, None, "cover")
    mock_ai.assert_not_called()


@patch("lib.enricher._fetch_ai_metadata")
@patch("lib.enricher._fetch_wikidata_series", return_value=(None, None))
@patch("lib.enricher._fetch_google_books_metadata", return_value={})
@patch("lib.enricher._fetch_open_library", return_value={})
def test_enrich_book_ai_fallback_used_when_provider_set(
    mock_ol, mock_gb, mock_wd, mock_ai, monkeypatch
):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    mock_ai.return_value = {"description": "AI description"}

    result = enrich_book("Title", "Author", None, None, "cover")
    assert result.description == "AI description"
    mock_ai.assert_called_once()


@patch("lib.enricher._fetch_ai_metadata")
@patch("lib.enricher._fetch_wikidata_series", return_value=(None, None))
@patch("lib.enricher._fetch_google_books_metadata")
@patch("lib.enricher._fetch_open_library")
def test_enrich_book_ai_never_used_for_series(
    mock_ol, mock_gb, mock_wd, mock_ai, monkeypatch
):
    monkeypatch.setenv("AI_PROVIDER", "openai")
    mock_ol.return_value = {}
    mock_gb.return_value = {}
    mock_ai.return_value = {"description": "AI description"}

    enrich_book("Title", "Author", None, None, "cover")

    # AI fallback must never influence series/group resolution.
    mock_wd.assert_called_once()
    assert mock_ai.call_count == 1


# -----------------------------
# _fetch_wikidata_series
# -----------------------------


@patch("lib.enricher._wikidata_query")
def test_fetch_wikidata_series_isbn_hit(mock_query):
    mock_query.return_value = [
        {"seriesLabel": {"value": "The Dragon Knight"}, "ordinal": {"value": "9"}}
    ]
    name, position = _fetch_wikidata_series("9781234567897", "Title", "Author")
    assert name == "The Dragon Knight"
    assert position == 9


@patch("lib.enricher._wikidata_query")
def test_fetch_wikidata_series_no_ordinal(mock_query):
    mock_query.return_value = [{"seriesLabel": {"value": "The Dragon Knight"}}]
    name, position = _fetch_wikidata_series("9781234567897", "Title", "Author")
    assert name == "The Dragon Knight"
    assert position is None


@patch("lib.enricher._wikidata_query", return_value=[])
def test_fetch_wikidata_series_miss(mock_query):
    name, position = _fetch_wikidata_series("9781234567897", "Title", "Author")
    assert (name, position) == (None, None)


@patch("lib.enricher._wikidata_query")
def test_fetch_wikidata_series_falls_back_to_title_author(mock_query):
    mock_query.side_effect = [[], [{"seriesLabel": {"value": "Some Series"}}]]
    name, position = _fetch_wikidata_series("9781234567897", "Title", "Author")
    assert name == "Some Series"
    assert mock_query.call_count == 2


# -----------------------------
# AI provider fallback
# -----------------------------


def test_fetch_ai_metadata_unknown_provider():
    assert _fetch_ai_metadata("made-up-provider", "Title", "Author", None) == {}


def test_fetch_openai_metadata_no_api_key():
    assert "OPENAI_API_KEY" not in os.environ
    assert _fetch_openai_metadata("Title", "Author", None) == {}


@patch("lib.enricher.requests.post")
def test_fetch_openai_metadata_hit(mock_post, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mock_post.return_value.raise_for_status = lambda: None
    mock_post.return_value.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"description": "AI desc", "publisher": "AI Pub", '
                        '"publish_date": "2020", "page_count": 42}'
                    )
                }
            }
        ]
    }

    result = _fetch_openai_metadata("Title", "Author", "9781234567897")
    assert result == {
        "description": "AI desc",
        "publisher": "AI Pub",
        "publish_date": "2020",
        "length_of": "42",
    }


@patch("lib.enricher.requests.post")
def test_fetch_openai_metadata_malformed_json(mock_post, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mock_post.return_value.raise_for_status = lambda: None
    mock_post.return_value.json.return_value = {
        "choices": [{"message": {"content": "not json"}}]
    }

    assert _fetch_openai_metadata("Title", "Author", None) == {}


@patch("lib.enricher.requests.post", side_effect=RuntimeError("boom"))
def test_fetch_openai_metadata_api_error(mock_post, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert _fetch_openai_metadata("Title", "Author", None) == {}
