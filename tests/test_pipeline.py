from unittest.mock import ANY, patch

from lib import EnrichmentResult

from chirp_to_libib.core import RunResult, main, run


@patch("chirp_to_libib.core.write_unresolved")
@patch("chirp_to_libib.core.write_csv")
@patch("chirp_to_libib.core.enrich_book", return_value=EnrichmentResult())
@patch("chirp_to_libib.core.sleep_between_requests")
@patch("chirp_to_libib.core.get_isbn", return_value="1234567890")
@patch("chirp_to_libib.core.scrape_chirp")
def test_pipeline_dry_run(
    mock_scrape,
    mock_get_isbn,
    mock_sleep,
    mock_enrich_book,
    mock_write_csv,
    mock_write_unresolved,
):
    mock_scrape.return_value = [
        ("Title A", "Author A", "coverA"),
        ("Title B", "Author B", "coverB"),
    ]

    with patch("sys.argv", ["prog", "--dry-run"]):
        main()

    # Credentials are no longer prompted for Chirp — login is manual via browser.
    # wait_fn is now threaded through from run()'s default; ANY sidesteps
    # coupling this assertion to the exact default function object.
    mock_scrape.assert_called_once_with(
        "", "", max_pages=None, wait_fn=ANY, cancel_fn=ANY
    )
    assert mock_get_isbn.call_count == 2
    assert mock_enrich_book.call_count == 2
    mock_write_csv.assert_not_called()
    mock_write_unresolved.assert_not_called()


@patch("chirp_to_libib.core.write_unresolved")
@patch("chirp_to_libib.core.write_csv")
@patch("chirp_to_libib.core.enrich_book")
@patch("chirp_to_libib.core.sleep_between_requests")
@patch("chirp_to_libib.core.get_isbn", return_value="1234567890")
@patch("chirp_to_libib.core.scrape_chirp")
def test_pipeline_no_enrich_skips_enrichment(
    mock_scrape,
    mock_get_isbn,
    mock_sleep,
    mock_enrich_book,
    mock_write_csv,
    mock_write_unresolved,
):
    mock_scrape.return_value = [("Title A", "Author A", "coverA")]

    with patch("sys.argv", ["prog", "--dry-run", "--no-enrich"]):
        main()

    mock_enrich_book.assert_not_called()


# ==========================
# run() — callable directly, no argparse (REFACTOR-6)
# ==========================


@patch("chirp_to_libib.core.write_unresolved", return_value=None)
@patch("chirp_to_libib.core.write_csv", return_value="/out/chirp_to_libib_x.csv")
@patch("chirp_to_libib.core.enrich_book", return_value=EnrichmentResult())
@patch("chirp_to_libib.core.sleep_between_requests")
@patch("chirp_to_libib.core.get_isbn", return_value="1234567890")
@patch("chirp_to_libib.core.scrape_chirp")
def test_run_returns_paths_and_counts(
    mock_scrape,
    mock_get_isbn,
    mock_sleep,
    mock_enrich_book,
    mock_write_csv,
    mock_write_unresolved,
):
    mock_scrape.return_value = [("Title A", "Author A", "coverA")]

    result = run(output_dir="/out")

    assert result.csv_path == "/out/chirp_to_libib_x.csv"
    assert result.unresolved_path is None
    assert result.total_books == 1
    assert result.resolved_count == 1


@patch("chirp_to_libib.core.write_unresolved")
@patch("chirp_to_libib.core.write_csv")
@patch("chirp_to_libib.core.enrich_book", return_value=EnrichmentResult())
@patch("chirp_to_libib.core.sleep_between_requests")
@patch("chirp_to_libib.core.get_isbn", return_value="1234567890")
@patch("chirp_to_libib.core.scrape_chirp")
def test_run_dry_run_returns_no_paths(
    mock_scrape,
    mock_get_isbn,
    mock_sleep,
    mock_enrich_book,
    mock_write_csv,
    mock_write_unresolved,
):
    mock_scrape.return_value = [("Title A", "Author A", "coverA")]

    result = run(dry_run=True)

    assert result.csv_path is None
    assert result.unresolved_path is None
    assert result.total_books == 1
    mock_write_csv.assert_not_called()


@patch("chirp_to_libib.core.scrape_chirp", return_value=[])
def test_run_no_books_scraped_returns_empty_result(mock_scrape):
    result = run()

    assert result == RunResult(
        csv_path=None, unresolved_path=None, total_books=0, resolved_count=0
    )


@patch("chirp_to_libib.core.scrape_chirp", return_value=[])
def test_run_passes_wait_fn_through_to_scrape(mock_scrape):
    def my_wait() -> None:
        pass

    run(wait_fn=my_wait)

    mock_scrape.assert_called_once_with(
        "", "", max_pages=None, wait_fn=my_wait, cancel_fn=ANY
    )
