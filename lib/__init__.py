# lib package — shared modules for ISBN resolution, Libib schema, and utilities.

from lib.cancellation import OperationCancelled
from lib.enricher import EnrichmentResult, enrich_book, format_series_notes
from lib.openlibrary import (
    LIBIB_HEADERS,
    classify_identifier,
    get_isbn,
    sleep_between_requests,
    dedupe_books_by_title,
    filter_invalid_books,
)

__all__ = [
    "LIBIB_HEADERS",
    "classify_identifier",
    "get_isbn",
    "sleep_between_requests",
    "dedupe_books_by_title",
    "filter_invalid_books",
    "EnrichmentResult",
    "enrich_book",
    "format_series_notes",
    "OperationCancelled",
]
