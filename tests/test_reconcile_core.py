import csv
import os
import tempfile
from unittest.mock import ANY, MagicMock, patch

import pytest

import libib_reconcile.review as review_module
from lib import LIBIB_HEADERS
from libib_reconcile.core import (
    ReconcileRunResult,
    _load_scrape_csv,
    parse_args,
    run,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
LIBIB_EXPORT_FIXTURE = os.path.join(FIXTURES, "libib_export_sample.csv")
KINDLE_SCRAPE_FIXTURE = os.path.join(FIXTURES, "scrape_kindle_sample.csv")


@pytest.fixture(autouse=True)
def _isolate_global_skip_list(tmp_path, monkeypatch):
    """run() writes a real review snapshot in several tests here, which
    touches the global skip list (see libib_reconcile/review.py) — redirect
    it to a throwaway path so tests never read the developer's real one."""
    monkeypatch.setattr(
        review_module, "_GLOBAL_SKIP_LIST_PATH", str(tmp_path / "reconcile_skips.json")
    )


# ==========================
# CLI
# ==========================


def test_cli_requires_libib():
    with patch("sys.argv", ["prog"]):
        with pytest.raises(SystemExit):
            parse_args()


def test_cli_libib_and_defaults():
    with patch("sys.argv", ["prog", "--libib", "export.csv"]):
        args = parse_args()
    assert args.libib == "export.csv"
    assert args.scrape is False
    assert args.dry_run is False
    assert args.no_enrich is False
    assert args.wait_for_rate_limits is False
    assert args.output_dir == "."
    assert set(args.providers) == {"chirp", "kindle", "kobo", "nook", "google"}


def test_cli_per_provider_paths():
    with patch(
        "sys.argv",
        ["prog", "--libib", "export.csv", "--kindle", "k.csv", "--kobo", "b.csv"],
    ):
        args = parse_args()
    assert args.kindle == "k.csv"
    assert args.kobo == "b.csv"
    assert args.chirp is None


def test_cli_providers_limits_choices():
    with patch(
        "sys.argv", ["prog", "--libib", "export.csv", "--providers", "kindle", "kobo"]
    ):
        args = parse_args()
    assert args.providers == ["kindle", "kobo"]


def test_cli_invalid_provider_rejected():
    with patch(
        "sys.argv", ["prog", "--libib", "export.csv", "--providers", "not-a-provider"]
    ):
        with pytest.raises(SystemExit):
            parse_args()


def test_cli_scrape_and_no_enrich_flags():
    with patch(
        "sys.argv", ["prog", "--libib", "export.csv", "--scrape", "--no-enrich"]
    ):
        args = parse_args()
    assert args.scrape is True
    assert args.no_enrich is True


def test_cli_wait_for_rate_limits_flag():
    with patch("sys.argv", ["prog", "--libib", "export.csv", "--wait-for-rate-limits"]):
        args = parse_args()
    assert args.wait_for_rate_limits is True


# ==========================
# _load_scrape_csv
# ==========================


def test_load_scrape_csv_reads_isbn_and_cover():
    books = _load_scrape_csv(KINDLE_SCRAPE_FIXTURE)
    assert len(books) == 2
    title, author, isbn, cover = books[0]
    assert title == "Project Hail Mary"
    assert author == "Andy Weir"
    assert isbn == "9780593135204"
    assert cover == "http://cover.example.com/phm.jpg"


def test_load_scrape_csv_prefers_ean13_over_upc10():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "scrape.csv")

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=LIBIB_HEADERS)
            writer.writeheader()
            row = {h: "" for h in LIBIB_HEADERS}
            row["title"] = "Both ISBNs"
            row["ean_isbn13"] = "9780593135204"
            row["upc_isbn10"] = "0593135202"
            writer.writerow(row)

        books = _load_scrape_csv(path)
    assert books[0][2] == "9780593135204"


def test_load_scrape_csv_decodes_html_entities():
    """Real case found live (2026-08-02): ~3% of a real Kindle export had a
    literal "&amp;" in the title/author fields (a double-encoded entity
    baked into the scraped CSV, from before the scrapers' own textContent
    fix) — decoded defensively here too so already-scraped CSVs benefit
    without a re-scrape."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "scrape.csv")

        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=LIBIB_HEADERS)
            writer.writeheader()
            row = {h: "" for h in LIBIB_HEADERS}
            row["title"] = "Terciel &amp; Elinor"
            row["creators"] = "Garth Nix &amp; Someone Else"
            writer.writerow(row)

        books = _load_scrape_csv(path)
    assert books[0][0] == "Terciel & Elinor"
    assert books[0][1] == "Garth Nix & Someone Else"


# ==========================
# run() — provider source validation
# ==========================


def test_run_raises_without_any_provider_source():
    with pytest.raises(ValueError, match="No provider source supplied"):
        run(libib_path=LIBIB_EXPORT_FIXTURE)


@patch("libib_reconcile.core.read_libib_export", return_value=[])
def test_run_accepts_a_single_existing_csv_path(mock_read):
    with tempfile.TemporaryDirectory() as tmp:
        result = run(
            libib_path=LIBIB_EXPORT_FIXTURE,
            kindle=KINDLE_SCRAPE_FIXTURE,
            output_dir=tmp,
            no_enrich=True,
        )
    assert isinstance(result, ReconcileRunResult)


# ==========================
# run() — dry-run writes nothing (TST-8)
# ==========================


@patch("libib_reconcile.core.write_ambiguous_report")
@patch("libib_reconcile.core.write_low_confidence_report")
@patch("libib_reconcile.core.write_orphan_report")
@patch("libib_reconcile.core.write_gap_csv")
@patch("libib_reconcile.core.write_reconciliation_report")
@patch("libib_reconcile.core.enrich_gap_books")
def test_run_dry_run_writes_no_files(
    mock_enrich_gap,
    mock_summary,
    mock_gap_csv,
    mock_orphan,
    mock_low_conf,
    mock_ambiguous,
):
    result = run(
        libib_path=LIBIB_EXPORT_FIXTURE, kindle=KINDLE_SCRAPE_FIXTURE, dry_run=True
    )

    mock_summary.assert_not_called()
    mock_gap_csv.assert_not_called()
    mock_orphan.assert_not_called()
    mock_low_conf.assert_not_called()
    mock_ambiguous.assert_not_called()
    mock_enrich_gap.assert_not_called()
    assert result.gap_csv_path is None
    assert result.summary_path is None
    assert result.total_gap_books >= 0


def test_run_dry_run_no_files_on_disk():
    with tempfile.TemporaryDirectory() as tmp:
        run(
            libib_path=LIBIB_EXPORT_FIXTURE,
            kindle=KINDLE_SCRAPE_FIXTURE,
            output_dir=tmp,
            dry_run=True,
        )
        assert os.listdir(tmp) == []


# ==========================
# run() — --scrape wiring
# ==========================


@patch("libib_reconcile.core.importlib.import_module")
def test_run_scrape_calls_scraper_run_with_no_enrich(mock_import_module):
    fake_module = MagicMock()
    fake_module.run.return_value = MagicMock(csv_path=KINDLE_SCRAPE_FIXTURE)
    mock_import_module.return_value = fake_module

    with tempfile.TemporaryDirectory() as tmp:
        run(
            libib_path=LIBIB_EXPORT_FIXTURE,
            output_dir=tmp,
            providers=["kindle"],
            scrape=True,
            dry_run=True,
        )

    mock_import_module.assert_called_once_with("kindle_to_libib.core")
    fake_module.run.assert_called_once_with(output_dir=tmp, no_enrich=True)


@patch("libib_reconcile.core.importlib.import_module")
def test_run_scrape_skips_unbuilt_google_provider(mock_import_module):
    with tempfile.TemporaryDirectory() as tmp:
        result = run(
            libib_path=LIBIB_EXPORT_FIXTURE,
            output_dir=tmp,
            providers=["google"],
            scrape=True,
            dry_run=True,
        )

    mock_import_module.assert_not_called()
    assert result.total_gap_books == 0


@patch("libib_reconcile.core.importlib.import_module")
def test_run_prefers_existing_path_over_scrape(mock_import_module):
    with tempfile.TemporaryDirectory() as tmp:
        run(
            libib_path=LIBIB_EXPORT_FIXTURE,
            output_dir=tmp,
            providers=["kindle"],
            scrape=True,
            kindle=KINDLE_SCRAPE_FIXTURE,
            dry_run=True,
        )

    mock_import_module.assert_not_called()


# ==========================
# Integration — fixture CSVs with a known gap (TST-9)
# ==========================


def test_run_integration_known_gap():
    with tempfile.TemporaryDirectory() as tmp:
        result = run(
            libib_path=LIBIB_EXPORT_FIXTURE,
            kindle=KINDLE_SCRAPE_FIXTURE,
            output_dir=tmp,
            no_enrich=True,
        )

        assert result.total_gap_books == 1
        assert result.gap_csv_path is not None

        with open(result.gap_csv_path, encoding="utf-8-sig") as f:
            gap_csv_text = f.read()
        assert "A Brand New Book" in gap_csv_text
        assert "Project Hail Mary" not in gap_csv_text

        assert result.summary_path is not None
        with open(result.summary_path, encoding="utf-8") as f:
            summary_text = f.read()
        assert "Missing (gap):     1" in summary_text

        assert result.review_snapshot_path is not None
        assert os.path.exists(result.review_snapshot_path)


# ==========================
# run() — review snapshot + cancel_fn (checkbook review feature)
# ==========================


def test_run_dry_run_has_no_review_snapshot_path():
    with tempfile.TemporaryDirectory() as tmp:
        result = run(
            libib_path=LIBIB_EXPORT_FIXTURE,
            kindle=KINDLE_SCRAPE_FIXTURE,
            output_dir=tmp,
            dry_run=True,
        )
    assert result.review_snapshot_path is None


@patch("libib_reconcile.core.enrich_gap_books")
@patch("libib_reconcile.core.enrich_missing_isbns")
def test_run_threads_cancel_fn_into_both_enrichment_stages(
    mock_enrich_missing_isbns, mock_enrich_gap_books
):
    mock_enrich_missing_isbns.side_effect = (
        lambda result, cancel_fn, wait_for_rate_limits: result
    )
    mock_enrich_gap_books.return_value = []

    def my_cancel_fn() -> bool:
        return False

    with tempfile.TemporaryDirectory() as tmp:
        run(
            libib_path=LIBIB_EXPORT_FIXTURE,
            kindle=KINDLE_SCRAPE_FIXTURE,
            output_dir=tmp,
            cancel_fn=my_cancel_fn,
        )

    mock_enrich_missing_isbns.assert_called_once_with(
        ANY, cancel_fn=my_cancel_fn, wait_for_rate_limits=False
    )
    mock_enrich_gap_books.assert_called_once_with(
        ANY, no_enrich=False, cancel_fn=my_cancel_fn, wait_for_rate_limits=False
    )
