# LibibTools — Project Backlog

## Overview

LibibTools is a **public, community-facing tool** on GitHub (`Jinniyah/libib-tools`).
It scrapes personal digital book libraries and exports Libib-compatible CSVs.

This backlog covers two workstreams:

1. **New scrapers** — `nook_to_libib` (Selenium + manual login) and
   `google_to_libib` (Google Books API + OAuth 2.0)
2. **`libib_reconcile`** — compares a Libib export against scrapes from all five
   providers, identifies missing books, enriches with ISBNs, and produces a
   ready-to-import gap CSV plus human-readable reports

For architectural context, tag schema findings, and data model decisions, see
`docs/CLAUDE.md`.

---

## Providers — Full Set

| Provider | Module | Approach | Tag |
|----------|--------|----------|-----|
| Chirp | `chirp_to_libib` | Selenium + manual login | `chirp,audiobook` |
| Kindle | `kindle_to_libib` | Selenium + automated login | `kindle,ebook` |
| Kobo | `kobo_to_libib` | Selenium + manual two-tab login | `kobo,ebook` |
| Nook | `nook_to_libib` | Selenium + manual login *(planned)* | `nook,ebook` |
| Google Books | `google_to_libib` | Google Books API + OAuth 2.0 *(planned)* | `google,ebook` |

---

## Session Plan

### New Scrapers

| Session | Module | Goal |
|---------|--------|------|
| **Nook-1** | `nook_to_libib` | Scaffold module; manual login flow; Selenium scraping of bn.com digital library; CSV output. Needs DOM screenshot first. |
| **Nook-2** | `nook_to_libib` | Tests, CI, README section, add `nook` to reconciler provider list |
| **Google-1** | `google_to_libib` | OAuth 2.0 setup guide; API client; paginate Purchased shelf (ID 7); CSV output |
| **Google-2** | `google_to_libib` | Tests, CI, README section, add `google` to reconciler provider list |

### Reconciler

| Session | Epics | Goal |
|---------|-------|------|
| **Rec-1** | Epic 1 + scaffolding | Parse Libib export; tag normalization; provider classification; filters. By end: can load and classify the real export. |
| **Rec-2** | Epic 2 | Matching engine: ISBN-exact first, then fuzzy fallback with confidence scoring. |
| **Rec-3** | Epics 3 + 4 | ISBN enrichment for gap books + all output files (gap CSV + reports). |
| **Rec-4** | Epics 5 + 6 | CLI, full test suite, CI integration. `python -m libib_reconcile` works. |
| **Rec-5** | Integration | Run against real data; tune thresholds; polish. |

---

## Status Key

| Symbol | Meaning |
|--------|---------|
| `[ ]` | Not started |
| `[~]` | In progress |
| `[x]` | Complete |

---

## Nook Scraper (`nook_to_libib`)

**Approach:** Selenium + manual login pause (same pattern as Chirp and Kobo).
B&N uses Akamai bot detection — do not attempt to automate login.
Library URL: `https://www.barnesandnoble.com/account/my-digital-library`

**⚠️ Before starting Nook-1:** Need a DevTools screenshot of the bn.com digital
library DOM (same as the Kobo screenshot that revealed `li.item-wrapper.book`).
Ask Jennifer to open the library page in Chrome, inspect an element, and share
a screenshot of the Elements panel.

### Nook-1 — Core scraper

- [ ] `NOOK-1` Scaffold `nook_to_libib/` with `__init__.py`, `__main__.py`, `core.py`
- [ ] `NOOK-2` Add `nook_to_libib` to `packages` in `pyproject.toml`
- [ ] `NOOK-3` `_build_driver()` — same anti-fingerprint flags as Chirp/Kobo
- [ ] `NOOK-4` `_login()` — manual pause flow; verify library grid visible before continuing
- [ ] `NOOK-5` `_parse_items()` — extract `(title, author, cover_url)` from DOM (selectors TBD from screenshot)
- [ ] `NOOK-6` `scrape_nook()` — full pagination loop with `--pages` limit support
- [ ] `NOOK-7` `resolve_isbns()` — reuse `get_isbn` + `sleep_between_requests` from `lib`
- [ ] `NOOK-8` `write_csv()` — 28-column Libib CSV, tag = `nook,ebook`, UTF-8-sig
- [ ] `NOOK-9` `write_unresolved()` — txt report of books without ISBNs
- [ ] `NOOK-10` `main()` + CLI: `--pages`, `--dry-run`, `--output-dir`
- [ ] `NOOK-11` Black-format and verify CI passes

### Nook-2 — Tests, CI, docs

- [ ] `NOOK-12` `tests/test_nook.py` — unit tests for `_parse_items`, dedup, filter, resolve_isbns, write_csv, write_unresolved
- [ ] `NOOK-13` Add `nook_to_libib` to `[tool.coverage.run] source` in `pyproject.toml`
- [ ] `NOOK-14` README — Nook section: manual login instructions, output files, CLI flags
- [ ] `NOOK-15` Update `docs/CLAUDE.md` — add Nook to providers table and login strategies
- [ ] `NOOK-16` Update `libib_reconcile` provider classifier to recognise `nook` tag
- [ ] `NOOK-17` Update `OUT-6` out-of-scope report to remove Nook (it now has a scraper)
- [ ] `NOOK-18` Full test suite passes; CI green

---

## Google Books Scraper (`google_to_libib`)

**Approach:** Google Books API — no Selenium, no browser automation.
Uses `mylibrary/bookshelves/7/volumes` (Purchased shelf, auto-populated, read-only).
Requires OAuth 2.0 one-time setup per user.

**New pip dependencies:** `google-api-python-client`, `google-auth-oauthlib`
Add to `requirements.txt` when scaffolding.

**Key API facts:**
- Base URL: `https://www.googleapis.com/books/v1/mylibrary/bookshelves/7/volumes`
- Auth: OAuth 2.0, scope `https://www.googleapis.com/auth/books`
- Rate limit: 1000 requests/day (free tier) — well above any personal library size
- Returns: title, authors, ISBNs (industryIdentifiers), thumbnail — **no Open Library lookup needed for most books**
- Pagination: `startIndex` + `maxResults` (max 40 per page)

### Google-1 — Core scraper

- [ ] `GOOG-1` Scaffold `google_to_libib/` with `__init__.py`, `__main__.py`, `core.py`
- [ ] `GOOG-2` Add `google_to_libib` to `packages` in `pyproject.toml`
- [ ] `GOOG-3` Add `google-api-python-client` and `google-auth-oauthlib` to `requirements.txt`
- [ ] `GOOG-4` OAuth 2.0 flow: load credentials from `~/.config/libibtools/google_token.json`; run browser consent on first use; auto-refresh thereafter
- [ ] `GOOG-5` `fetch_all_books()` — paginate through Purchased shelf; extract `(title, author, isbn, cover_url)` directly from API response
- [ ] `GOOG-6` ISBN extraction from `industryIdentifiers` array — prefer ISBN-13, fall back to ISBN-10, then Open Library lookup if neither present
- [ ] `GOOG-7` `write_csv()` — 28-column Libib CSV, tag = `google,ebook`, UTF-8-sig
- [ ] `GOOG-8` `write_unresolved()` — txt report of books without ISBNs
- [ ] `GOOG-9` `main()` + CLI: `--dry-run`, `--output-dir`, `--credentials PATH` (default: `~/.config/libibtools/google_credentials.json`)
- [ ] `GOOG-10` Black-format and verify CI passes

### Google-2 — Tests, CI, docs

- [ ] `GOOG-11` `tests/test_google.py` — unit tests with mocked API responses: pagination, ISBN extraction, CSV output, unresolved output
- [ ] `GOOG-12` Add `google_to_libib` to `[tool.coverage.run] source` in `pyproject.toml`
- [ ] `GOOG-13` README — Google Books section: OAuth setup walkthrough (Google Cloud Console steps), credentials file location, CLI flags
- [ ] `GOOG-14` README — note that `--pages` is not needed (API returns full library in one paginated batch)
- [ ] `GOOG-15` Update `docs/CLAUDE.md` — add Google to providers table
- [ ] `GOOG-16` Update `libib_reconcile` provider classifier to recognise `google` tag
- [ ] `GOOG-17` Full test suite passes; CI green

---

## Reconciler (`libib_reconcile`)

### Epic 1 — Libib CSV Parser
**File:** `libib_reconcile/libib_reader.py`
**Session:** Rec-1

- [ ] `LIB-1` Parse Libib export CSV; handle UTF-8-sig encoding and quoted fields with embedded commas
- [ ] `LIB-2` Tag normalizer: lowercase → split on `,` → strip whitespace → return `set[str]`
- [ ] `LIB-3` Provider classifier: scan normalized tag set; return `set[str]` of detected providers (`kindle`, `kobo`, `chirp`, `nook`, `google`, `digital_unknown`)
- [ ] `LIB-4` Entry filter: skip any entry whose tag set contains `deleted` or `removed`
- [ ] `LIB-5` Entry filter: skip entries with no digital provider keywords at all (physical-only books)
- [ ] `LIB-6` Flag `digital`-only entries (tag set contains `digital` but no named provider) as ambiguous
- [ ] `LIB-7` Extract existing ISBNs from `ean_isbn13` and `upc_isbn10` fields for use as primary match keys

### Scaffolding tasks (also Rec-1)
- [ ] `SCAFFOLD-1` Create `libib_reconcile/` with `__init__.py`, `__main__.py`, `core.py`, `libib_reader.py`, `reconciler.py`, `isbn_enricher.py`, `output.py`
- [ ] `SCAFFOLD-2` Add `libib_reconcile` to `packages` in `pyproject.toml`
- [ ] `SCAFFOLD-3` Create `tests/test_libib_reader.py` — tag normalizer, provider classifier, all filters
- [ ] `SCAFFOLD-4` Verify full test suite still passes after scaffolding

### Epic 2 — Reconciliation Engine
**File:** `libib_reconcile/reconciler.py`
**Session:** Rec-2

- [ ] `REC-1` ISBN-exact match: if both sides have an ISBN, match on that first; highest confidence
- [ ] `REC-2` Title+author fuzzy fallback using `_title_is_plausible()` from `lib/openlibrary.py`
- [ ] `REC-3` Confidence scoring: `exact_isbn` = high, `fuzzy_title_author` = medium, `title_only` = low
- [ ] `REC-4` Provider-aware matching: `kindle, kobo` entry checks both scrapes; match on either = matched
- [ ] `REC-5` Cross-format: `chirp, kindle` entry matches either Chirp or Kindle scrape
- [ ] `REC-6` Classify each scraped book: `matched` or `missing_from_libib`
- [ ] `REC-7` Classify each Libib entry: `matched`, `libib_only` (orphan), `ambiguous`, or `out_of_scope`
- [ ] `REC-8` Dedup scraped books before comparing (reuse `dedupe_books_by_title` from `lib`)
- [ ] `REC-9` Unit tests in `tests/test_reconcile.py`

### Epic 3 — ISBN Enrichment
**File:** `libib_reconcile/isbn_enricher.py`
**Session:** Rec-3

- [ ] `ISBN-1` For each `missing_from_libib` book, call `get_isbn(title, author)` from `lib`
- [ ] `ISBN-2` Skip Open Library lookup if book already has an ISBN (Google API provides these directly)
- [ ] `ISBN-3` Respect rate limiting via `sleep_between_requests()` from `lib`
- [ ] `ISBN-4` Log progress at `ISBN_LOG_INTERVAL = 25`
- [ ] `ISBN-5` Track and report enrichment rate
- [ ] `ISBN-6` Unit tests: mock `get_isbn` and verify enrichment

### Epic 4 — Output Files
**File:** `libib_reconcile/output.py`
**Session:** Rec-3

- [ ] `OUT-1` **Gap CSV**: missing books ready to import into Libib; full 28-column format; correct `tags` per provider; ISBN-populated where available
- [ ] `OUT-2` **Reconciliation report** (`.txt`): summary counts + per-provider breakdown
- [ ] `OUT-3` **Orphan report** (`.txt`): Libib entries not found in any scrape
- [ ] `OUT-4` **Low-confidence match report** (`.txt`): fuzzy matches needing human review
- [ ] `OUT-5` **Ambiguous report** (`.txt`): `digital`-only entries with no named provider
- [ ] `OUT-6` Timestamp all output files: `reconcile_YYYY-MM-DD_HH-MM_<type>.{csv,txt}`
- [ ] `OUT-7` Unit tests: CSV columns, tag assignment, dry-run writes nothing

### Epic 5 — CLI & Orchestration
**Files:** `libib_reconcile/__main__.py`, `libib_reconcile/core.py`
**Session:** Rec-4

- [ ] `CLI-1` `--libib PATH` — Libib export CSV (required)
- [ ] `CLI-2` `--scrape` — trigger live scrapes of all five providers
- [ ] `CLI-3` `--kindle PATH` / `--kobo PATH` / `--chirp PATH` / `--nook PATH` / `--google PATH` — accept pre-existing scrape CSVs
- [ ] `CLI-4` `--output-dir PATH` — consistent with other tools
- [ ] `CLI-5` `--providers kindle kobo chirp nook google` — limit to specific providers
- [ ] `CLI-6` `--dry-run` — no output files written
- [ ] `CLI-7` Validate at least one provider source supplied; exit with clear error if not
- [ ] `CLI-8` `__main__.py`: `from .core import main` + `if __name__ == "__main__": main()`

### Epic 6 — Tests & CI
**Session:** Rec-4

- [ ] `TST-1` Tag normalization edge cases: empty, whitespace, `deleted`/`removed`, mixed case
- [ ] `TST-2` Provider classification: all five providers; `digital`-only; physical-only
- [ ] `TST-3` ISBN-exact matching
- [ ] `TST-4` Fuzzy matching: subtitle variants, threshold behaviour
- [ ] `TST-5` Provider-aware matching: `kindle, kobo` matches either scrape
- [ ] `TST-6` Cross-format: `chirp, kindle` matches either
- [ ] `TST-7` Gap CSV: correct columns, correct tags, ISBN populated when available
- [ ] `TST-8` Dry-run: no files on disk
- [ ] `TST-9` Integration test: fixture CSVs with known gap → assert correct counts
- [ ] `TST-10` Add `libib_reconcile` to `[tool.coverage.run] source` in `pyproject.toml`
- [ ] `TST-11` Full suite passes; CI green

### Rec-5 — Integration & Polish
*Not broken into tickets yet — depends on real data results.*

- Run against real Libib export + all five provider scrapes
- Review reconciliation report and orphan report for accuracy
- Tune `_title_is_plausible()` threshold
- Review low-confidence matches
- Import gap CSV into Libib and verify it loads cleanly
- Update `docs/CLAUDE.md` session history
- Update `README.md` to document `libib_reconcile`

---

## Known Constraints & Edge Cases

### Kindle scraper status
The `kindle_to_libib` scraper is working correctly. It scrapes library metadata
from the Amazon web page via Selenium and is unaffected by any DRM or epub tooling
(e.g. Epubor). No changes needed.

### Google Books — ISBNs already provided
The Google Books API returns `industryIdentifiers` (ISBN-13 and/or ISBN-10) directly
in the volume metadata. Open Library lookup should be skipped for Google books that
already have an ISBN, saving API quota and time.

### Google Books — credentials file location
Store OAuth token at `~/.config/libibtools/google_token.json` and credentials at
`~/.config/libibtools/google_credentials.json`. Never commit these to git — add both
paths to `.gitignore`. Document the one-time Cloud Console setup clearly in the README
so other users can follow it.

### Nook DOM selectors
DOM selectors for `nook_to_libib` are unknown until a DevTools screenshot is obtained.
Do not guess — wait for the screenshot before writing `_parse_items()`.

### Tag edge cases from real Libib export
- Physical + digital combo: `digital, kindle, new, paperback` → treat as digital
- `digital` + physical, no named provider: `digital, hardback` → `digital_unknown` ambiguous
- `removed` standalone → skip
- No tags at all → skip (treat as physical/unknown)

### Matching edge cases
- Series books: "Dune (Dune Chronicles, #1)" vs "Dune" — `_title_is_plausible` handles via word overlap
- Omnibus/box sets: Libib entry "Mistborn Books 1-3" vs three individual entries — will not match; orphan expected
- Duplicate ISBNs in scrape (same book on two platforms) — dedup before matching
