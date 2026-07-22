from unittest.mock import patch

from chirp_to_libib.core import parse_args as chirp_parse
from kindle_to_libib.core import parse_args as kindle_parse


def test_chirp_cli_pages():
    with patch("sys.argv", ["prog", "--pages", "3"]):
        args = chirp_parse()
        assert args.pages == 3


def test_kindle_cli_pages():
    with patch("sys.argv", ["prog", "--pages", "2"]):
        args = kindle_parse()
        assert args.pages == 2


def test_chirp_cli_dry_run():
    with patch("sys.argv", ["prog", "--dry-run"]):
        args = chirp_parse()
        assert args.dry_run is True


def test_kindle_cli_output_dir():
    with patch("sys.argv", ["prog", "--output-dir", "out"]):
        args = kindle_parse()
        assert args.output_dir == "out"


def test_chirp_cli_no_enrich():
    with patch("sys.argv", ["prog", "--no-enrich"]):
        args = chirp_parse()
        assert args.no_enrich is True


def test_kindle_cli_no_enrich_default_false():
    with patch("sys.argv", ["prog"]):
        args = kindle_parse()
        assert args.no_enrich is False
