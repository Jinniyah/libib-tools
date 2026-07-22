import os

from libib_reconcile.libib_reader import (
    LIBIB_EXPORT_HEADERS,
    LibibEntry,
    classify_providers,
    extract_isbns,
    is_ambiguous,
    normalize_tags,
    read_libib_export,
    should_skip,
)

FIXTURE_PATH = os.path.join(
    os.path.dirname(__file__), "fixtures", "libib_export_sample.csv"
)


# ==========================
# normalize_tags
# ==========================


def test_normalize_tags_basic():
    assert normalize_tags("digital, kindle") == {"digital", "kindle"}


def test_normalize_tags_mixed_case():
    assert normalize_tags("Digital, Kindle") == {"digital", "kindle"}


def test_normalize_tags_extra_whitespace():
    assert normalize_tags("  digital ,  kindle  ") == {"digital", "kindle"}


def test_normalize_tags_empty_string():
    assert normalize_tags("") == set()


def test_normalize_tags_none_like_empty():
    assert normalize_tags("   ") == set()


def test_normalize_tags_trailing_comma():
    assert normalize_tags("digital, kindle,") == {"digital", "kindle"}


# ==========================
# classify_providers
# ==========================


def test_classify_providers_single():
    assert classify_providers({"digital", "kindle"}) == {"kindle"}


def test_classify_providers_multi():
    assert classify_providers({"digital", "kindle", "kobo"}) == {"kindle", "kobo"}


def test_classify_providers_audiobook_maps_to_chirp():
    assert classify_providers({"audiobook", "chirp"}) == {"chirp"}


def test_classify_providers_cross_format():
    assert classify_providers({"chirp", "digital", "kindle", "nook"}) == {
        "chirp",
        "kindle",
        "nook",
    }


def test_classify_providers_google():
    assert classify_providers({"digital", "google"}) == {"google"}


def test_classify_providers_digital_only_is_unknown():
    assert classify_providers({"digital"}) == {"digital_unknown"}


def test_classify_providers_physical_only_is_empty():
    assert classify_providers({"new", "paperback"}) == set()


# ==========================
# should_skip
# ==========================


def test_should_skip_deleted():
    assert should_skip({"deleted", "digital", "kindle"}) is True


def test_should_skip_removed():
    assert should_skip({"removed"}) is True


def test_should_skip_physical_only():
    assert should_skip({"new", "paperback"}) is True


def test_should_skip_no_tags():
    assert should_skip(set()) is True


def test_should_skip_digital_kept():
    assert should_skip({"digital", "kindle"}) is False


def test_should_skip_digital_alone_kept():
    """A bare 'digital' tag is ambiguous, not skippable — it still represents
    a digital copy, just of an unknown platform."""
    assert should_skip({"digital"}) is False


# ==========================
# is_ambiguous
# ==========================


def test_is_ambiguous_true():
    assert is_ambiguous({"digital"}, {"digital_unknown"}) is True


def test_is_ambiguous_false_with_named_provider():
    assert is_ambiguous({"digital", "kindle"}, {"kindle"}) is False


def test_is_ambiguous_false_physical():
    assert is_ambiguous({"new", "paperback"}, set()) is False


# ==========================
# extract_isbns
# ==========================


def test_extract_isbns_both_present():
    row = {"ean_isbn13": "9780593135204", "upc_isbn10": "0593135202"}
    assert extract_isbns(row) == ("9780593135204", "0593135202")


def test_extract_isbns_missing_columns():
    assert extract_isbns({}) == ("", "")


def test_extract_isbns_strips_whitespace():
    row = {"ean_isbn13": " 9780593135204 ", "upc_isbn10": ""}
    assert extract_isbns(row) == ("9780593135204", "")


# ==========================
# read_libib_export — integration over the fixture file
# ==========================


def test_read_libib_export_returns_entries_for_every_row():
    entries = read_libib_export(FIXTURE_PATH)
    assert len(entries) == 15
    assert all(isinstance(e, LibibEntry) for e in entries)


def test_read_libib_export_skip_count():
    entries = read_libib_export(FIXTURE_PATH)
    skipped_titles = {e.title for e in entries if e.skip}
    assert skipped_titles == {
        "Deleted Book",
        "Removed Book",
        "Physical Paperback",
        "Old Hardback",
        "No Tags Book",
    }


def test_read_libib_export_ambiguous_count():
    entries = read_libib_export(FIXTURE_PATH)
    ambiguous_titles = {e.title for e in entries if e.ambiguous}
    assert ambiguous_titles == {"Unknown Platform Book"}


def test_read_libib_export_multi_provider_entry():
    entries = read_libib_export(FIXTURE_PATH)
    mistborn = next(e for e in entries if e.title == "Mistborn: The Final Empire")
    assert mistborn.providers == {"kindle", "kobo"}
    assert mistborn.ean_isbn13 == "9780765350381"
    assert mistborn.skip is False
    assert mistborn.ambiguous is False


def test_read_libib_export_cross_format_entry():
    entries = read_libib_export(FIXTURE_PATH)
    piranesi = next(e for e in entries if e.title == "Piranesi")
    assert piranesi.providers == {"chirp", "kindle", "nook"}


def test_read_libib_export_audiobook_chirp_entry():
    entries = read_libib_export(FIXTURE_PATH)
    wind = next(e for e in entries if e.title == "The Name of the Wind")
    assert wind.providers == {"chirp"}


def test_read_libib_export_mixed_case_tags_normalized():
    entries = read_libib_export(FIXTURE_PATH)
    case_test = next(e for e in entries if e.title == "Case Test Book")
    assert case_test.tags == {"digital", "kindle"}
    assert case_test.providers == {"kindle"}


def test_read_libib_export_google_entry_detected():
    entries = read_libib_export(FIXTURE_PATH)
    google_entry = next(e for e in entries if e.title == "Google Books Entry")
    assert google_entry.providers == {"google"}
    assert google_entry.skip is False


def test_read_libib_export_digital_plus_physical_not_skipped():
    """A digital+physical combo entry (e.g. 'digital, kindle, new, hardback')
    must be treated as digital, not skipped as physical."""
    entries = read_libib_export(FIXTURE_PATH)
    combo = next(e for e in entries if e.title == "Digital Plus Physical")
    assert combo.skip is False
    assert combo.providers == {"kindle"}


def test_read_libib_export_no_isbn_entry_has_empty_strings():
    entries = read_libib_export(FIXTURE_PATH)
    fifth_season = next(e for e in entries if e.title == "The Fifth Season")
    assert fifth_season.ean_isbn13 == ""
    assert fifth_season.upc_isbn10 == ""


def test_libib_export_headers_constant_matches_fixture():
    with open(FIXTURE_PATH, encoding="utf-8-sig") as f:
        header_line = f.readline().strip()
    assert header_line.split(",") == LIBIB_EXPORT_HEADERS
