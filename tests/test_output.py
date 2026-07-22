import csv
import os
import tempfile

from lib import EnrichmentResult, LIBIB_HEADERS, classify_identifier

from chirp_to_libib.core import (
    write_csv as chirp_write_csv,
    write_unresolved as chirp_write_unresolved,
)
from kindle_to_libib.core import (
    write_csv as kindle_write_csv,
    write_unresolved as kindle_write_unresolved,
)

# Stand-in for a book with no enrichment data — matches --no-enrich output.
EMPTY = EnrichmentResult()

# ==========================
# IDENTIFIER CLASSIFICATION
# ==========================


def test_classify_isbn13():
    upc, ean = classify_identifier("9781402894626")
    assert upc == ""
    assert ean == "9781402894626"


def test_classify_isbn10():
    upc, ean = classify_identifier("1402894627")
    assert upc == "1402894627"
    assert ean == ""


def test_classify_isbn10_with_x():
    upc, ean = classify_identifier("059035342X")
    assert upc == "059035342X"
    assert ean == ""


def test_classify_empty():
    upc, ean = classify_identifier("")
    assert upc == ""
    assert ean == ""


def test_classify_unknown_falls_back_to_upc():
    upc, ean = classify_identifier("12345")
    assert upc == "12345"
    assert ean == ""


# ==========================
# CSV HEADER STRUCTURE
# ==========================

EXPECTED_HEADERS = [
    "added",
    "creators",
    "began_date",
    "call_numbers",
    "completed_date",
    "copies",
    "description",
    "group",
    "upc_isbn10",
    "ean_isbn13",
    "ddc",
    "lcc",
    "lccn",
    "oclc",
    "lexile",
    "length_of",
    "number_of_discs",
    "aspect_ratio",
    "notes",
    "price",
    "publish_date",
    "publisher",
    "rating",
    "review",
    "review_date",
    "status",
    "tags",
    "title",
]


def test_libib_headers_match_spec():
    assert LIBIB_HEADERS == EXPECTED_HEADERS


# ==========================
# CSV OUTPUT TESTS — CHIRP
# ==========================


def test_chirp_write_csv_headers():
    records = [("Title A", "Author A", "1234567890", "coverA", EMPTY)]
    with tempfile.TemporaryDirectory() as tmp:
        path = chirp_write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            headers = next(csv.reader(f))
        assert headers == EXPECTED_HEADERS


def test_chirp_write_csv_mapping():
    records = [
        ("Title A", "Author A", "1234567890", "http://cover.example.com/a.jpg", EMPTY),
        ("Title B", "Author B", None, "", EMPTY),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = chirp_write_csv(records, tmp)
        assert os.path.exists(path)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == 2

        # Row 1: ISBN-10 goes into upc_isbn10
        assert rows[0]["title"] == "Title A"
        assert rows[0]["creators"] == "Author A"
        assert rows[0]["upc_isbn10"] == "1234567890"
        assert rows[0]["ean_isbn13"] == ""
        assert rows[0]["tags"] == "chirp,audiobook"
        assert rows[0]["notes"] == "http://cover.example.com/a.jpg"

        # Row 2: no ISBN — both identifier fields empty
        assert rows[1]["upc_isbn10"] == ""
        assert rows[1]["ean_isbn13"] == ""
        assert rows[1]["notes"] == ""


def test_chirp_write_csv_isbn13():
    records = [("Title C", "Author C", "9781234567897", "cover", EMPTY)]
    with tempfile.TemporaryDirectory() as tmp:
        path = chirp_write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["upc_isbn10"] == ""
        assert rows[0]["ean_isbn13"] == "9781234567897"


def test_chirp_write_csv_empty_columns():
    """All non-mapped columns must be empty strings."""
    records = [("Title A", "Author A", "1234567890", "cover", EMPTY)]
    with tempfile.TemporaryDirectory() as tmp:
        path = chirp_write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        non_mapped = [
            "added",
            "began_date",
            "call_numbers",
            "completed_date",
            "copies",
            "description",
            "group",
            "ddc",
            "lcc",
            "lccn",
            "oclc",
            "lexile",
            "length_of",
            "number_of_discs",
            "aspect_ratio",
            "price",
            "publish_date",
            "publisher",
            "rating",
            "review",
            "review_date",
            "status",
        ]
        for col in non_mapped:
            assert rows[0][col] == "", f"Expected empty string for column '{col}'"


# ==========================
# CSV OUTPUT TESTS — KINDLE
# ==========================


def test_kindle_write_csv_headers():
    records = [("Title A", "Author A", "9781402894626", "coverA", EMPTY)]
    with tempfile.TemporaryDirectory() as tmp:
        path = kindle_write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            headers = next(csv.reader(f))
        assert headers == EXPECTED_HEADERS


def test_kindle_write_csv_mapping():
    records = [
        (
            "Title A",
            "Author A",
            "9781402894626",
            "http://cover.example.com/a.jpg",
            EMPTY,
        ),
        ("Title B", "Author B", None, "", EMPTY),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = kindle_write_csv(records, tmp)
        assert os.path.exists(path)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == 2

        # Row 1: ISBN-13 goes into ean_isbn13
        assert rows[0]["title"] == "Title A"
        assert rows[0]["creators"] == "Author A"
        assert rows[0]["upc_isbn10"] == ""
        assert rows[0]["ean_isbn13"] == "9781402894626"
        assert rows[0]["tags"] == "kindle,ebook"
        assert rows[0]["notes"] == "http://cover.example.com/a.jpg"

        # Row 2: no ISBN
        assert rows[1]["upc_isbn10"] == ""
        assert rows[1]["ean_isbn13"] == ""


def test_kindle_write_csv_isbn10():
    records = [("Title D", "Author D", "1402894627", "cover", EMPTY)]
    with tempfile.TemporaryDirectory() as tmp:
        path = kindle_write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        assert rows[0]["upc_isbn10"] == "1402894627"
        assert rows[0]["ean_isbn13"] == ""


def test_kindle_write_csv_empty_columns():
    """All non-mapped columns must be empty strings."""
    records = [("Title A", "Author A", "9781402894626", "cover", EMPTY)]
    with tempfile.TemporaryDirectory() as tmp:
        path = kindle_write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        non_mapped = [
            "added",
            "began_date",
            "call_numbers",
            "completed_date",
            "copies",
            "description",
            "group",
            "ddc",
            "lcc",
            "lccn",
            "oclc",
            "lexile",
            "length_of",
            "number_of_discs",
            "aspect_ratio",
            "price",
            "publish_date",
            "publisher",
            "rating",
            "review",
            "review_date",
            "status",
        ]
        for col in non_mapped:
            assert rows[0][col] == "", f"Expected empty string for column '{col}'"


# ==========================
# ENRICHMENT FIELD MAPPING
# ==========================


def test_chirp_write_csv_enrichment_mapping():
    enrichment = EnrichmentResult(
        description="A great book.",
        publisher="Tor Books",
        publish_date="2020",
        length_of="320",
        series_name="The Dragon Knight",
        series_position=9,
    )
    records = [("Title A", "Author A", "1234567890", "cover-url", enrichment)]
    with tempfile.TemporaryDirectory() as tmp:
        path = chirp_write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

    assert rows[0]["description"] == "A great book."
    assert rows[0]["publisher"] == "Tor Books"
    assert rows[0]["publish_date"] == "2020"
    assert rows[0]["length_of"] == "320"
    assert rows[0]["group"] == "The Dragon Knight"
    assert rows[0]["notes"] == (
        "Series: The Dragon Knight #009 || Additional Notes: cover-url"
    )


def test_chirp_write_csv_enrichment_fills_missing_isbn():
    enrichment = EnrichmentResult(isbn13="9781234567897")
    records = [("Title A", "Author A", None, "cover", enrichment)]
    with tempfile.TemporaryDirectory() as tmp:
        path = chirp_write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

    assert rows[0]["ean_isbn13"] == "9781234567897"
    assert rows[0]["upc_isbn10"] == ""


def test_kindle_write_csv_enrichment_mapping():
    enrichment = EnrichmentResult(
        description="Another great book.",
        publisher="Ace",
        publish_date="1999",
        length_of="200",
        series_name="Some Series",
        series_position=None,
    )
    records = [("Title A", "Author A", "9781402894626", "cover-url", enrichment)]
    with tempfile.TemporaryDirectory() as tmp:
        path = kindle_write_csv(records, tmp)
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

    assert rows[0]["description"] == "Another great book."
    assert rows[0]["group"] == "Some Series"
    assert rows[0]["notes"] == (
        "Series: Some Series #ZZZ || Additional Notes: cover-url"
    )


# ==========================
# UNRESOLVED OUTPUT TESTS
# ==========================


def test_chirp_write_unresolved():
    records = [
        ("Title A", "Author A", "1234567890", "coverA", EMPTY),
        ("Title B", "Author B", None, "coverB", EMPTY),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = chirp_write_unresolved(records, tmp)
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert "Title B" in text
        assert "Title A" not in text


def test_kindle_write_unresolved():
    records = [
        ("Title A", "Author A", "9781402894626", "coverA", EMPTY),
        ("Title B", "Author B", None, "coverB", EMPTY),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = kindle_write_unresolved(records, tmp)
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert "Title B" in text
        assert "Title A" not in text
