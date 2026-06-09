# LibibTools

![Tests](https://github.com/Jinniyah/LibibTools/actions/workflows/tests.yml/badge.svg)
![Coverage](https://img.shields.io/badge/coverage-generated-blue)

Tools for automating and enriching personal library management workflows.  
This repository includes three Python packages that scrape your digital book libraries and export Libib‑compatible CSVs:

- **chirp‑to‑libib** — scrapes your [Chirp Books](https://www.chirpbooks.com) audiobook library
- **kindle‑to‑libib** — scrapes your [Amazon Kindle](https://www.amazon.com/hz/mycd/digital-console/contentlist/booksAll/dateDsc/) ebook library
- **kobo‑to‑libib** — scrapes your [Rakuten Kobo](https://www.kobo.com) ebook library

All three tools share a common `lib/` layer for ISBN resolution, deduplication, and filtering.

---

# Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Shared Modules](#shared-modules)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Credentials](#credentials)
- [Usage](#usage)
- [Options](#options)
- [Output Files](#output-files)
- [Importing into Libib](#importing-into-libib)
- [Configuration](#configuration)
- [Development Workflow](#development-workflow)
- [Makefile Tasks](#makefile-tasks)
- [Troubleshooting](#troubleshooting)
- [Privacy](#privacy)
- [Project Structure](#project-structure)
- [Contributing](#contributing)
- [Versioning](#versioning)
- [License](#license)

---

# Overview

LibibTools automates the process of exporting your digital book libraries into a format compatible with **Libib**, including:

- Browser-based library scraping via Selenium
- ISBN lookup via Open Library with retry and fallback logic
- High‑resolution cover URL extraction
- Full 28-column Libib-compatible CSV generation (UTF‑8 with BOM)
- Unresolved ISBN reporting

All three scrapers share a common library (`lib/`) for ISBN resolution, deduplication, and filtering logic, keeping provider-specific code minimal and focused.

---

# Features

- Manual login support for sites with bot-detection (Chirp, Kobo)
- Automated login for sites that support it (Kindle)
- Secure credential handling (env vars or interactive prompt) for Kindle
- Multi‑page scraping with configurable page limits
- ISBN resolution with author+title and title-only fallback
- Full 28-column Libib-compatible CSV output
- Full‑resolution cover URLs stored in the `notes` field
- Deduplication and invalid entry filtering
- Automated tests with CI
- Coverage reporting
- Configurable runtime behaviour

---

## Shared Modules

The `lib/` directory contains shared logic used by all providers:

- **`lib/openlibrary.py`** — Open Library ISBN lookup with exponential backoff, title plausibility matching, ISBN-10/ISBN-13 validation, shared `LIBIB_HEADERS` schema, and `classify_identifier()` for routing identifiers to the correct CSV column
- **`lib/__init__.py`** — Re-exports all shared symbols for clean provider imports

---

# Architecture

```
+---------------------------+     +---------------------------+     +---------------------------+
|  chirp_to_libib CLI       |     |  kindle_to_libib CLI      |     |  kobo_to_libib CLI        |
|  __main__.py / core.py    |     |  __main__.py / core.py    |     |  __main__.py / core.py    |
|  - Manual login (browser) |     |  - Automated login        |     |  - Manual login (browser) |
|  - Chirp page scraping    |     |  - Kindle page scraping   |     |  - Kobo page scraping     |
+------------+--------------+     +-------------+-------------+     +-------------+-------------+
             |                                  |                                 |
             +----------------------------------+---------------------------------+
                                                |
                                                v
                             +------------------+------------------+
                             |                lib/                 |
                             |  - LIBIB_HEADERS schema             |
                             |  - classify_identifier()            |
                             |  - ISBN lookup (Open Library)       |
                             |  - Retry / exponential backoff      |
                             |  - Title plausibility matching      |
                             |  - Deduplication                    |
                             |  - Invalid entry filtering          |
                             +-------------------------------------+
                                                |
                                                v
                             +-------------------------------------+
                             |          Output Artifacts           |
                             |  28-column CSV + unresolved log     |
                             +-------------------------------------+
```

Key design principles:

- **DRY**: All shared logic lives in `lib/` — no duplication between providers
- **Idempotent**: Running multiple times produces predictable results
- **Observable**: Structured logging at key checkpoints throughout the pipeline
- **Extensible**: Adding a new provider requires only a new `core.py` consuming the shared `lib/`

---

# Requirements

- Python **3.10+**
- Google Chrome installed
- `webdriver-manager` (installed via `requirements.txt`)

---

# Installation

```bash
git clone https://github.com/Jinniyah/LibibTools.git
cd LibibTools

python -m venv venv
source venv/bin/activate   # macOS/Linux
venv\Scripts\activate      # Windows

pip install -r requirements.txt
```

---

# Credentials

## Chirp

Chirp uses bot-detection that blocks automated login. **No credentials are required** — the script opens the Chirp login page in a browser window and pauses, letting you log in manually. Once you are logged in and your library is visible, press Enter in the terminal to continue.

## Kindle

Kindle supports automated login. Set credentials as environment variables before running. If not set, the script will prompt interactively.

### macOS / Linux
```bash
export KINDLE_EMAIL="you@example.com"
export KINDLE_PASSWORD="yourpassword"
```

### Windows
```cmd
set KINDLE_EMAIL=you@example.com
set KINDLE_PASSWORD=yourpassword
```

## Kobo

Kobo uses hCaptcha on its sign-in page (`authorize.kobo.com`) which fingerprints Selenium-opened tabs and blocks automated login. **No credentials are required** — the script uses a manual two-tab workaround. See [Kobo — hCaptcha workaround](#kobo--hcaptcha-workaround) below for the full explanation.

---

# Usage

## Chirp

```bash
python -m chirp_to_libib
```

When the browser opens, log in to Chirp manually (completing any CAPTCHA if shown). Once your library is fully loaded, press Enter in the terminal.

## Kindle

```bash
python -m kindle_to_libib
```

## Kobo

```bash
python -m kobo_to_libib
```

The script will open the Kobo sign-in page and guide you through the two-tab login process (see [Kobo — hCaptcha workaround](#kobo--hcaptcha-workaround)).

### Limit to N pages
```bash
python -m chirp_to_libib --pages 3
python -m kindle_to_libib --pages 3
python -m kobo_to_libib --pages 3
```

### Dry run (resolve ISBNs but do not write files)
```bash
python -m chirp_to_libib --dry-run
python -m kindle_to_libib --dry-run
python -m kobo_to_libib --dry-run
```

### Custom output directory
```bash
python -m chirp_to_libib --output-dir ~/Documents/Libib
python -m kindle_to_libib --output-dir ~/Documents/Libib
python -m kobo_to_libib --output-dir ~/Documents/Libib
```

---

# Options

All providers support the same CLI flags:

| Option | Description |
|--------|-------------|
| `--pages N` | Scrape only the first N pages |
| `--dry-run` | Resolve ISBNs but do not write output files |
| `--output-dir PATH` | Directory for output files (default: current directory) |

---

# Output Files

### Chirp

| File | Description |
|------|-------------|
| `chirp_to_libib_YYYY-MM-DD_HH-MM.csv` | Libib‑compatible CSV |
| `chirp_to_libib_unresolved_YYYY-MM-DD_HH-MM.txt` | Titles with missing ISBNs |

### Kindle

| File | Description |
|------|-------------|
| `kindle_to_libib_YYYY-MM-DD_HH-MM.csv` | Libib‑compatible CSV |
| `kindle_to_libib_unresolved_YYYY-MM-DD_HH-MM.txt` | Titles with missing ISBNs |

### Kobo

| File | Description |
|------|-------------|
| `kobo_to_libib_YYYY-MM-DD_HH-MM.csv` | Libib‑compatible CSV |
| `kobo_to_libib_unresolved_YYYY-MM-DD_HH-MM.txt` | Titles with missing ISBNs |

### CSV Columns

All tools produce a full Libib-compatible CSV with all 28 import columns. Only the following fields are populated; all others are left empty for you to fill in Libib:

| Column | Populated from |
|--------|----------------|
| `title` | Book title |
| `creators` | Author(s) |
| `upc_isbn10` | ISBN-10 or UPC (if identifier is 10 characters) |
| `ean_isbn13` | ISBN-13 or EAN (if identifier is 13 digits) |
| `tags` | `chirp,audiobook` · `kindle,ebook` · `kobo,ebook` |
| `notes` | Full-resolution cover image URL |

---

# Importing into Libib

1. Log in at <https://www.libib.com>
2. Open your library
3. **Add Items → Import CSV**
4. Upload the generated CSV
5. Map columns if prompted

---

# Configuration

### `lib/openlibrary.py`

| Constant | Default | Description |
|----------|---------|-------------|
| `ISBN_DELAY_RANGE` | `(0.8, 1.6)` | Randomised delay between Open Library API requests (seconds) |

### `chirp_to_libib/core.py`

| Constant | Default | Description |
|----------|---------|-------------|
| `ISBN_LOG_INTERVAL` | `25` | Log ISBN progress every N books |
| `PAGE_WAIT_TIMEOUT` | `30` | Selenium wait timeout after manual login (seconds) |

### `kindle_to_libib/core.py`

| Constant | Default | Description |
|----------|---------|-------------|
| `ISBN_LOG_INTERVAL` | `25` | Log ISBN progress every N books |
| `PAGE_WAIT_TIMEOUT` | `20` | Selenium wait timeout (seconds) |

### `kobo_to_libib/core.py`

| Constant | Default | Description |
|----------|---------|-------------|
| `ISBN_LOG_INTERVAL` | `25` | Log ISBN progress every N books |
| `PAGE_WAIT_TIMEOUT` | `30` | Selenium wait timeout after manual login (seconds) |

---

# Development Workflow

### Install dev dependencies

```bash
pip install -r requirements-dev.txt
```

### Run tests

```bash
pytest
```

### Run coverage

```bash
pytest --cov=chirp_to_libib --cov=kindle_to_libib --cov=kobo_to_libib --cov=lib --cov-report=term-missing
```

### Linting

```bash
ruff check .
```

### Formatting

```bash
black .
```

### Type checking

```bash
mypy chirp_to_libib kindle_to_libib kobo_to_libib lib
```

---

# Makefile Tasks

```bash
make test        # run tests
make lint        # run ruff and black format check
make format      # run black
make typecheck   # run mypy across all packages
make coverage    # run full coverage suite
```

---

# Troubleshooting

**Chirp — CAPTCHA or bot-detection blocking login**  
This is expected. The script opens the browser and pauses for you to log in manually. Complete any CAPTCHA in the browser, wait until your library is visible, then press Enter in the terminal.

**Chirp — "Could not confirm a successful login"**  
You pressed Enter before your library fully loaded. Re-run the script and wait until the page is completely loaded before pressing Enter.

**Kindle — Login fails**  
Amazon may have updated their login page selectors. Update `_login` in `kindle_to_libib/core.py`. You may also need to complete a two-factor authentication step in the browser window before the script can proceed.

---

## Kobo — hCaptcha workaround

Kobo's sign-in page is hosted on `authorize.kobo.com` and protected by **hCaptcha**. hCaptcha fingerprints the browser environment when the page first loads and flags Selenium-opened tabs as bots — even with the standard `navigator.webdriver` suppression — silently blocking login before you can submit the form.

The workaround is a **manual two-tab flow**:

1. Selenium opens the sign-in page in **tab 1**. hCaptcha fingerprints this tab and blocks it — do not attempt to log in here.
2. You **copy the URL** from the address bar, open a **new tab manually** (Ctrl+T), and paste the URL.
3. Because tab 2 is opened by you (not Selenium), and it inherits the session cookies already set by tab 1, hCaptcha evaluates it as a legitimate browser session and allows login.
4. You log in successfully in tab 2, navigate to **My Books → Books**, wait for the grid to load, then press Enter in the terminal.
5. The script switches its Selenium focus to tab 2 and continues scraping normally.

The terminal will walk you through each step when you run the tool.

**Kobo — "Could not find any book cards on the page"**  
You pressed Enter before your library finished loading, or you are not on the Books page. Make sure the grid of book covers is fully visible before pressing Enter.

**Kobo — Books page shows 0 items after scraping**  
The Kobo library DOM may have changed. Check that `li.item-wrapper.book` still appears in the page source and update `_SEL_BOOK_ITEM` in `kobo_to_libib/core.py` if needed.

---

**All providers — No books scraped**  
The site's HTML structure may have changed. Update the parsing selectors in `_parse_items` in the relevant `core.py`.

**All providers — ChromeDriver mismatch**
```bash
pip install --upgrade webdriver-manager
```

**All providers — Missing ISBNs**  
Open Library coverage varies by title. Use the unresolved report for manual lookup.

---

# Privacy

- Only Chirp, Amazon, Kobo, and Open Library are contacted during a run
- Output files contain personal library data and are excluded via `.gitignore`
- Kindle credentials are never written to disk and are cleared from memory immediately after the scrape completes
- Chirp and Kobo require no credentials — login is performed manually in the browser

---

# Project Structure

```text
LibibTools/
├── chirp_to_libib/
│   ├── __init__.py
│   ├── __main__.py
│   └── core.py
├── kindle_to_libib/
│   ├── __init__.py
│   ├── __main__.py
│   └── core.py
├── kobo_to_libib/
│   ├── __init__.py
│   ├── __main__.py
│   └── core.py
├── lib/
│   ├── __init__.py
│   └── openlibrary.py
├── tests/
│   ├── test_chirp.py
│   ├── test_cli.py
│   ├── test_dedupe_filter.py
│   ├── test_isbn_utils.py
│   ├── test_kindle.py
│   ├── test_kobo.py
│   ├── test_openlibrary.py
│   ├── test_output.py
│   ├── test_pipeline.py
│   └── test_scrape.py
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml
├── Makefile
└── README.md
```


---

# Contributing

Contributions are welcome.

1. Fork the repository
2. Create a feature branch
3. Write tests for any new behaviour
4. Ensure linting, formatting, and type checks pass
5. Submit a pull request

---

# Versioning

This project follows **Semantic Versioning (SemVer)**:

```
MAJOR.MINOR.PATCH
```

- **MAJOR**: Breaking changes
- **MINOR**: New features
- **PATCH**: Bug fixes

---

# License

MIT — see `LICENSE`.
