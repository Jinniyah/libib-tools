# CLAUDE.md — LibibTools Project Reference

This file exists to keep Claude oriented between sessions on the LibibTools project.
Read this at the start of every session before touching any code.

---

## Project Overview

**LibibTools** is a public Python monorepo at `C:\Users\jinni\source\repos\libib-tools`
(GitHub: `Jinniyah/libib-tools`). It is designed as a community tool, not just for
personal use — keep that in mind when making architectural decisions.

It contains tools for scraping personal digital book libraries and exporting
Libib-compatible CSVs, plus a forthcoming reconciliation tool.

---

## Repository Layout

```
libib-tools/
├── chirp_to_libib/        # Chirp audiobook scraper (complete)
│   ├── __init__.py
│   ├── __main__.py
│   └── core.py
├── kindle_to_libib/       # Amazon Kindle scraper (complete)
│   ├── __init__.py
│   ├── __main__.py
│   └── core.py
├── kobo_to_libib/         # Rakuten Kobo scraper (complete)
│   ├── __init__.py
│   ├── __main__.py
│   └── core.py
├── nook_to_libib/         # Barnes & Noble Nook scraper (complete)
│   ├── __init__.py
│   ├── __main__.py
│   └── core.py
├── google_to_libib/       # Google Play Books scraper (PLANNED)
├── lib/                   # Shared logic (all scrapers import from here)
│   ├── __init__.py
│   ├── openlibrary.py
│   ├── enricher.py        # Metadata + series enrichment (complete)
│   ├── http_retry.py      # Shared GET-with-retry (429/Retry-After handling, cancel_fn)
│   └── cancellation.py    # OperationCancelled — shared cancel signal
├── libib_reconcile/       # Reconciliation tool (COMPLETE — Rec-1 through Rec-4; Rec-5 integration/polish remains)
│   ├── __init__.py
│   ├── __main__.py
│   ├── core.py            # Orchestration/CLI — complete
│   ├── libib_reader.py    # Libib export parsing/classification — complete
│   ├── reconciler.py      # Matching engine — complete
│   ├── isbn_enricher.py   # Gap-book ISBN resolution — complete
│   └── output.py          # Gap CSV + reports — complete
├── webapp/                # Local web GUI (GUI-Backend-1..5, GUI-Frontend-1..3 COMPLETE;
│   │                       # GUI-Reconcile/Settings/Security/Polish sessions remain)
│   ├── __init__.py
│   ├── __main__.py        # python -m webapp
│   ├── main.py             # uvicorn.run(), 127.0.0.1:8000 only
│   ├── app.py              # FastAPI app factory + all routes (see "The webapp Module" below)
│   ├── jobs/
│   │   ├── __init__.py
│   │   ├── registry.py    # Job dataclass, JobRegistry (one active job per provider)
│   │   ├── runner.py      # start_job() — spawns a thread, builds wait_fn
│   │   └── log_bridge.py  # bridges scraper logging.info(...) calls to a job's log_queue
│   ├── templates/
│   │   ├── base.html      # nav/footer layout, links the one shared stylesheet
│   │   ├── dashboard.html # per-tool cards + status badges
│   │   └── scrape.html    # run-a-scraper page (form, log panel, login-wait UI)
│   └── static/
│       ├── style.css      # the ONLY stylesheet — design tokens + component classes
│       └── app.js          # the ONLY client-side script — EventSource + fetch() wiring
├── tests/
│   ├── conftest.py
│   ├── test_chirp.py
│   ├── test_cli.py
│   ├── test_dedupe_filter.py
│   ├── test_enricher.py   # Enrich-1 tests (complete)
│   ├── test_isbn_enricher.py  # Rec-3 tests (complete)
│   ├── test_isbn_utils.py
│   ├── test_kindle.py
│   ├── test_kobo.py
│   ├── test_libib_reader.py  # Rec-1 tests (complete)
│   ├── test_nook.py       # Nook-1/Nook-2 tests (complete)
│   ├── test_openlibrary.py
│   ├── test_output.py
│   ├── test_pipeline.py
│   ├── test_reconcile.py  # Rec-2 tests (complete)
│   ├── test_reconcile_core.py  # Rec-4 tests (complete) — CLI, dry-run, --scrape wiring, integration
│   ├── test_reconcile_output.py  # Rec-3 tests (complete)
│   ├── test_scrape.py
│   ├── test_job_runner.py           # GUI-Backend-2 — fake provider, no Selenium
│   ├── test_log_bridge.py           # GUI-Backend-3 — thread isolation
│   ├── test_webapp_dashboard.py     # GUI-Backend-1/Frontend-2 — cards, status badges
│   ├── test_webapp_events.py        # GUI-Backend-3 — SSE streaming
│   ├── test_webapp_job_control.py   # GUI-Backend-4 — continue/cancel/shutdown
│   ├── test_webapp_scrape_dispatch.py  # GUI-Backend-5 — dispatch + downloads
│   ├── test_webapp_scrape_page.py   # GUI-Frontend-3 — page route, job detail, status SSE
│   └── fixtures/
│       ├── libib_export_sample.csv  # synthetic export fixture — NOT the real 7026-row file
│       └── scrape_kindle_sample.csv  # synthetic scrape fixture (LIBIB_HEADERS schema), Rec-4
├── docs/
│   ├── CLAUDE.md          # This file
│   └── backlog.md         # Full project backlog
├── libib_library_export_20260609_225934.csv   # Real Libib export (reference only)
├── pyproject.toml
├── requirements.txt
├── requirements-dev.txt
├── Makefile
└── README.md
```

---

## Toolchain & CI Rules

These are non-negotiable. CI will fail if any are violated.

| Tool | Rule |
|------|------|
| **Black** | All `.py` files must pass `black --check .` — run Black before committing |
| **Ruff** | Linting via `.ruff.toml` — line length 100, target py312 |
| **mypy** | Type annotations required on all function signatures |
| **pytest** | Full suite must pass — `pytest` from repo root |
| **CI** | GitHub Actions runs Black, pytest on every push |

**Never commit code that hasn't been Black-formatted.** This is the #1 cause of CI
failures. Use `filesystem:write_file` (full file rewrite) rather than
`filesystem:edit_file` when file content may have drifted — it's more reliable.
Always verify with `black --check` in the sandbox before writing to the repo.

---

## Providers — Full Set

| Provider | Module | Status | Approach | Tag |
|----------|--------|--------|----------|-----|
| Chirp | `chirp_to_libib` | ✅ Complete | Selenium + manual login | `chirp,audiobook` |
| Kindle | `kindle_to_libib` | ✅ Complete | Selenium + automated login | `kindle,ebook` |
| Kobo | `kobo_to_libib` | ✅ Complete | Selenium + manual two-tab login | `kobo,ebook` |
| Nook | `nook_to_libib` | ✅ Complete | Selenium + manual login | `nook,ebook` |
| Google Books | `google_to_libib` | 🔲 Planned | Google Books API + OAuth 2.0 | `google,ebook` |

---

## Shared Library (`lib/`)

All scrapers import exclusively from `lib/`. Never import from `lib.openlibrary`
or `lib.enricher` directly in new code — always use `from lib import ...`.

### Key exports from `lib/`

| Symbol | Description |
|--------|-------------|
| `LIBIB_HEADERS` | Ordered list of all 28 Libib CSV column names |
| `classify_identifier(isbn)` | Returns `(upc_isbn10, ean_isbn13)` tuple |
| `get_isbn(title, author)` | Open Library ISBN lookup with retry/fallback |
| `sleep_between_requests()` | Randomized delay (0.8–1.6s) for rate limiting |
| `dedupe_books_by_title(books)` | Remove duplicate `(title, author, cover)` tuples |
| `filter_invalid_books(books)` | Drop empty/garbage titles |
| `enrich_book(...)` | Metadata + series enrichment — see Enricher section *(PLANNED)* |
| `format_series_notes(...)` | Format series prefix for `notes` column *(PLANNED)* |
| `OperationCancelled` | Shared cancellation exception (`lib/cancellation.py`) — raised by `get_isbn()`/`enrich_book()`/scraper cores when a caller-supplied `cancel_fn` reports `True`; caught by `webapp/jobs/runner.py` |

### Internal helpers in `lib/openlibrary.py` (not exported, but available for reuse)

| Symbol | Description |
|--------|-------------|
| `_title_is_plausible(query, returned, threshold)` | Fuzzy title match — **reuse in reconciler** |
| `_valid_isbn13(s)` | Validates ISBN-13 checksum |
| `_valid_isbn10(s)` | Validates ISBN-10 checksum |
| `_best_isbn(isbns)` | Picks best ISBN from a list (prefers ISBN-13) |

### `lib/http_retry.py` (not exported via `lib/__init__.py` — internal to `lib/`)

`request_json(url, context, *, params=None, headers=None, max_retries=3,
base_backoff=2.0, cancel_fn=None)` — the single GET-with-retry helper used by
both `lib/enricher.py`'s `_http_get_json` and `lib/openlibrary.py`'s
`_ol_query` (previously duplicated between the two, and already drifted: 3 vs
4 `max_retries`). Handles HTTP 429 distinctly — honors `Retry-After` when
present, otherwise waits at least 5s, since the plain exponential backoff
used for other errors is provably too short against Google Books' actual
rate-limit window (observed 3 consecutive 429s at 2s/4s/8s spacing). Accepts
an optional `cancel_fn` so a cancel request lands within ~1s even mid-retry,
instead of waiting out the full backoff/Retry-After chain.

---

## Scraper Architecture

### Selenium scrapers — Chirp, Kindle, Kobo

All three follow the identical pipeline, now wrapped by `run()` (see
"CLI entry point: `run()` vs `main()`" below):

```
_build_driver() → _login() → scrape_*()
    ↓
list of (title, author, cover_url) tuples
    ↓
filter_invalid_books()
    ↓
dedupe_books_by_title()
    ↓
resolve_isbns()  [calls get_isbn + sleep_between_requests per book]
    ↓
enrich_books()   [calls enrich_book per book; skipped if --no-enrich]
    ↓
write_csv()  [28-column Libib CSV, UTF-8-sig]
write_unresolved()  [txt report of books with no ISBN]
```

### Selenium scraper — Nook (ISBN already in the DOM)

Nook's confirmed DOM carries the ISBN-13 on the book tile itself (`data-test`
attribute), so its pipeline diverges from Chirp/Kindle/Kobo — `resolve_isbns()`
is a fallback for the rare missing case, not the primary ISBN source:

```
_build_driver() → _login() → scrape_nook()
    ↓
list of (title, author, isbn, cover_url) tuples   [isbn already known from DOM]
    ↓
_dedupe_and_filter()  [wraps filter_invalid_books/dedupe_books_by_title, which are
                        typed for 3-tuples — isbn is swapped into the "cover" slot
                        for that pass, then covers are re-attached by ISBN after,
                        since ISBN is a more reliable key than title]
    ↓
resolve_isbns()  [trusts the scraped ISBN; only calls get_isbn() if missing]
    ↓
enrich_books()   [same as the other three; skipped if --no-enrich]
    ↓
write_csv() / write_unresolved()
```

### CLI entry point: `run()` vs `main()` (all four scrapers, since Rec-4a)

Each scraper's pipeline above lives inside a callable `run()`, not inline in
`main()`:

```python
def run(
    *, pages=None, dry_run=False, output_dir=".", no_enrich=False,
    wait_fn=_default_wait,   # Chirp/Kobo/Nook only — Kindle has no wait_fn param
    cancel_fn=lambda: False,  # all four — checked between pages/books/retries (GUI-BACKEND-4a)
) -> RunResult: ...

def main() -> None:
    args = parse_args()
    ...
    result = run(pages=args.pages, dry_run=args.dry_run, ...)
    if result.csv_path:
        print(f"\nUpload '{result.csv_path}' to Libib to update your collection.")
```

`RunResult` (`csv_path, unresolved_path, total_books, resolved_count` — all
`Optional[str]`/`int`) is a dataclass exception to the tuple-only data model,
same justification as `EnrichmentResult`/`LibibEntry`: callers (CLI, reconciler,
future GUI) need named fields, not positional ones. `main()` is now a thin
wrapper — `parse_args()` → `run(...)` → print — and is deliberately still
excluded from the coverage omit list's *reasoning* even though it stays
literally listed in `pyproject.toml`'s omit config: it's trivial glue, `run()`
is where the real logic (and the new direct tests) live.

**The `wait_fn` mechanism (Chirp/Kobo/Nook only):** `_login()` no longer prints
anything itself — each scraper's own `_default_wait()` bundles that scraper's
exact instructional text plus a blocking `input()`, and `_login(driver,
wait_fn=_default_wait)` just calls `wait_fn()`. This is how the CLI behavior
stays byte-for-byte identical while making the blocking mechanism swappable:
a future GUI passes a `wait_fn` that flips a job to `waiting_for_login` and
polls a `threading.Event` instead of blocking on stdin. Kindle has no `wait_fn`
— its login is automated, not a manual pause — so `run()`'s credentials handling
stays exactly as it was via `_prompt_credentials()` (env var first, prompt
fallback), deliberately with no email/password parameters on `run()` itself,
consistent with "credentials never pass through a browser form" from the GUI
security design.

Pagination is unconfirmed for Nook (no next-page control observed at typical
library sizes) — `scrape_nook()` scrapes a single page load. `--pages` is still
accepted on the CLI for consistency but is currently a no-op.

### Google Books scraper (API-based, no Selenium)

```
_load_credentials() → _authorize() → fetch_all_books()
    ↓
list of (title, author, isbn, cover_url, description, publisher, publish_date, page_count) tuples
    [ISBNs + metadata come directly from API]
    ↓
filter_invalid_books()
    ↓
dedupe_books_by_title()
    ↓
resolve_isbns()  [skips books that already have an ISBN from the API]
    ↓
enrich_books()   [metadata fields already populated; only Wikidata series query runs]
    ↓
write_csv() / write_unresolved()
```

### Data model
All scrapers use plain tuples — **no dataclasses or ORM**:
- Scrape output: `list[tuple[str, str, str]]` → `(title, author, cover_url)`
- After ISBN resolution: `list[tuple[str, str, Optional[str], str]]` → `(title, author, isbn, cover_url)`
- After enrichment (all four Selenium scrapers): `list[tuple[str, str, Optional[str], str, EnrichmentResult]]`
- Nook's scrape output is already the 4-tuple `(title, author, isbn, cover_url)` — its DOM
  provides the ISBN up front, so it skips straight to `_dedupe_and_filter()` instead of
  the 3-tuple `filter_invalid_books()`/`dedupe_books_by_title()` calls the others use directly
- Google Books may produce `(title, author, isbn, cover_url, description, publisher, publish_date, page_count)` directly from the API
- `EnrichmentResult` is a dataclass defined in `lib/enricher.py` — see Enricher section below

### Login strategies

| Scraper | Strategy |
|---------|----------|
| Chirp | Manual login pause — bot detection blocks automation |
| Kindle | Automated login (email/password via env vars or prompt) |
| Kobo | Manual two-tab login — hCaptcha blocks Selenium tab 1; user opens tab 2 by copying URL from address bar |
| Nook | Manual login pause — B&N Akamai bot detection; same approach as Chirp |
| Google Books | No browser login — OAuth 2.0 consent flow on first run; token auto-refreshes |

---

## Enricher (`lib/enricher.py`) — COMPLETE

### Purpose
Runs after `resolve_isbns()` and before `write_csv()` in every scraper pipeline.
Populates fields that scrapers leave blank and resolves series information.

Each scraper defines its own `enrich_books()` in `core.py` (same pattern as
`resolve_isbns()` — a thin per-book loop with progress logging), which calls the
shared `enrich_book()` from `lib/enricher.py` per book. `enrich_book()` itself
orchestrates three internal fetchers: `_fetch_open_library()`,
`_fetch_google_books_metadata()`, and `_fetch_wikidata_series()`, plus the
optional `_fetch_ai_metadata()` fallback. All three scrapers (Chirp, Kindle, Kobo)
have `enrich_books()` wired into `main()`, gated by `--no-enrich`; when that flag
is set, a shared `_NULL_ENRICHMENT = EnrichmentResult()` constant is used instead
so `write_csv()` doesn't need a separate code path.

### `EnrichmentResult` dataclass

```python
@dataclass
class EnrichmentResult:
    isbn13: str | None
    isbn10: str | None
    description: str | None
    publisher: str | None
    publish_date: str | None
    length_of: str | None       # page count as string
    series_name: str | None
    series_position: int | None # raw integer; None if unknown
```

### Public API (exported from `lib/`)

```python
def enrich_book(
    title: str,
    author: str,
    isbn13: str | None,
    isbn10: str | None,
    existing_notes: str,
) -> EnrichmentResult: ...

def format_series_notes(
    series_name: str | None,
    series_position: int | None,
    existing_notes: str,
) -> str: ...
```

### Series notes format

```
Series: The Dragon Knight #009 || Additional Notes: <original notes>
Series: The Dragon Knight #ZZZ || Additional Notes: <original notes>
```

- Position is always zero-padded to **3 digits** (`{pos:03d}`)
- If series name is known but position is `None`: use `#ZZZ`
- If no series data found: `notes` is unchanged; `group` is left blank

### Lookup sources and fallback chain

**Metadata** (description, publisher, publish_date, length_of, missing ISBNs):
```
Open Library (ISBN lookup first, then title+author search)
  → Google Books public metadata API (no auth required)
  → AI provider fallback (optional, env-var enabled — see AI Provider Fallback section)
  → leave blank
```

**Series** (series_name → `group`; series_position → prepended to `notes`):
```
Wikidata SPARQL — query by ISBN-13 (wdt:P212) first, then title+author
  → series name from wdt:P179 label; position from wdt:P1545
  → if series found but no position: series_name set, series_position = None → #ZZZ stamp
  → if no series record: series_name = None → notes unchanged, group blank
```

### Price policy
**Price is never fetched.** No free API has reliable coverage. If `price` is already
present in a row from the scraper (unlikely but possible), it is preserved. Otherwise
the column stays blank.

### Google Books shortcut
Books from `google_to_libib` already have description, publisher, publish_date, and
page count from the API. The enricher detects populated fields and skips Open Library
and Google Books metadata calls for those books, running only the Wikidata series query.

### `--no-enrich` flag
Chirp, Kindle, and Kobo support `--no-enrich` to skip the enrichment step entirely
(Nook and Google will get this from the start when they're scaffolded — see
ENR-14/ENR-15 in `docs/backlog.md`). Useful for fast runs or offline use.

---

## Nook Scraper — Key Notes

- Library URL: `https://nook.barnesandnoble.com/my_library/ebook` — this is where the
  confirmed DOM selectors were captured from (2026-07-21), not the
  `barnesandnoble.com/account/my-digital-library` URL mentioned in early planning notes
- B&N uses **Akamai** bot detection — manual login pause, same pattern as Chirp
  (not Kobo's two-tab trick, which exists specifically to defeat hCaptcha)
- DOM selectors (confirmed):
  - Container: `li[data-test]` — the ISBN-13 is the `data-test` attribute value itself
  - Title: `div.title > a`, read `data-product-title` (full title; the `<li>`'s own
    display text is ellipsis-truncated, do not use it)
  - Author: sibling `a[href*='barnesandnoble.com/search?q=']`, link text is the author
  - Cover: `img[data-bntrack='LinkedImage']`, read `src` directly (no `srcset` needed)
- **ISBN comes from the DOM, not Open Library** — `resolve_isbns()` here is a fallback
  for the rare book missing a `data-test` value, unlike Chirp/Kindle/Kobo where every
  book needs a live lookup. See [Selenium scraper — Nook](#selenium-scraper--nook-isbn-already-in-the-dom).
- Pagination is unconfirmed at typical library sizes — `scrape_nook()` scrapes a single
  page load; `--pages` is accepted for CLI consistency but currently has no effect
- Module structure otherwise follows `kobo_to_libib`
- Enrichment (`enrich_books()`, `--no-enrich`) was wired in from the initial scaffold,
  not retrofitted — see ENR-14 in backlog

---

## Google Books Scraper — Key Notes

- API endpoint: `GET https://www.googleapis.com/books/v1/mylibrary/bookshelves/7/volumes`
  - Shelf 7 = "Purchased" — auto-populated, read-only, can't be manually altered
- Auth scope: `https://www.googleapis.com/auth/books`
- New pip deps: `google-api-python-client`, `google-auth-oauthlib`
- Credentials: `~/.config/libibtools/google_credentials.json` (from Google Cloud Console)
- Token: `~/.config/libibtools/google_token.json` (auto-created on first run, auto-refreshes)
- **Never commit credentials or token to git** — add both to `.gitignore`
- API returns ISBNs in `industryIdentifiers` array — prefer ISBN-13, fall back to ISBN-10
- API also returns description, publisher, publishedDate, pageCount — pass these through
  so enricher skips redundant metadata lookups (only Wikidata series query runs)
- Pagination: `startIndex` + `maxResults=40`; loop until `totalItems` exhausted
- Rate limit: 1000 requests/day (free tier) — no issue for personal libraries
- **No `--pages` flag needed** — the API returns the full library; just paginate to completion
- The one-time Cloud Console setup (enable Books API, create OAuth credentials) must be
  documented clearly in the README for community users
- Include enrichment step in initial scaffold (see ENR-15 in backlog)

---

## Libib CSV Schema

The export and import schema share the same 28 columns (`LIBIB_HEADERS`):

```
added, creators, began_date, call_numbers, completed_date, copies,
description, group, upc_isbn10, ean_isbn13, ddc, lcc, lccn, oclc,
lexile, length_of, number_of_discs, aspect_ratio, notes, price,
publish_date, publisher, rating, review, review_date, status, tags, title
```

Scrapers populate: `title`, `creators`, `upc_isbn10`, `ean_isbn13`, `tags`, `notes` (cover URL).

Enricher additionally populates: `description`, `publisher`, `publish_date`, `length_of`,
`group` (series name), and prepends series info to `notes`.

---

## Libib Export — Real Data Findings

The file `libib_library_export_20260609_225934.csv` is Jennifer's real Libib export.

### Tag patterns observed
Tags are free-form comma-separated strings. Normalize by: lowercase → split on `,` → strip each part.

| Tag pattern (normalized) | Meaning |
|--------------------------|---------|
| `digital, kindle` | Kindle ebook only |
| `digital, kobo` | Kobo ebook only |
| `digital, kindle, kobo` | Owned on both Kindle and Kobo |
| `audiobook, chirp` | Chirp audiobook only |
| `audiobook, chirp, digital, kindle` | Both audio (Chirp) and ebook (Kindle) |
| `audiobook, chirp, hardback` | Chirp audio + physical copy |
| `digital, kindle, nook` | Kindle + Nook |
| `chirp, digital, kindle, nook` | Multi-platform |
| `digital` | Digital but provider unknown |
| `digital, kindle, kobo, new, paperback` | Digital + physical |
| `new, paperback` / `hardback, used` etc. | Physical only → **SKIP** |
| `deleted, ...` | Marked deleted → **SKIP** |
| `removed` | Marked removed → **SKIP** |

### Provider detection keywords (reconciler)
Scan tag set for these keywords:
- `kindle` → Kindle scrape
- `kobo` → Kobo scrape
- `chirp` or `audiobook` → Chirp scrape
- `nook` → Nook scrape
- `google` → Google Books scrape
- `digital` present but none of the above → ambiguous (report separately)
- No digital keyword at all → physical only → skip entirely

### One entry per book (by design)
A book owned on both Kindle and Kobo has ONE Libib entry tagged `kindle, kobo`.
The reconciler must match it against either scrape — a hit on either counts as matched.

### ISBNs in the export
`ean_isbn13` and `upc_isbn10` are often populated. **Always try ISBN-exact match first**
before fuzzy title matching.

---

## The `libib_reconcile` Module (COMPLETE — Rec-1 through Rec-4, 2026-07-22)

`python -m libib_reconcile --libib <export.csv> --kindle <scrape.csv> [...]`
works end-to-end. See `docs/backlog.md` for the full backlog, including the
`Rec-4a` core refactor this depended on, `Rec-5` (integration against real
data — not yet done), and the full Web GUI (`webapp/`) plan that follows.
High-level summary:

**Purpose:** Compare a Libib export CSV against scrapes from all five providers,
identify missing books, enrich with ISBNs, and produce a ready-to-import gap CSV
plus human-readable reports.

**Critical schema note:** the real Libib *export* CSV is a **different schema**
from `lib.LIBIB_HEADERS` (the *import* schema scrapers write). Export headers use
`length`/`began`/`completed` where the import schema uses
`length_of`/`began_date`/`completed_date`, plus export-only columns
(`item_type`, `first_name`, `last_name`, `collection`, `number_of_players`,
`age_group`, `ensemble`, `esrb`) that don't exist on import at all. This is
captured as `LIBIB_EXPORT_HEADERS` in `libib_reconcile/libib_reader.py` — do not
conflate it with `lib.LIBIB_HEADERS`. The real reference file
(`libib_library_export_20260609_225934.csv`, 7026 rows) is gitignored/local-only;
tests use a small hand-crafted fixture, `tests/fixtures/libib_export_sample.csv`
(15 rows, one per edge case), which is explicitly **not** covered by the blanket
`*.csv` gitignore rule — see the `.gitignore` exception for `tests/fixtures/*.csv`.

**File structure:**
```
libib_reconcile/
├── __init__.py
├── __main__.py
├── core.py           # Orchestration / CLI — COMPLETE (Rec-4)
├── libib_reader.py   # Parse and classify Libib export CSV — COMPLETE (Rec-1)
├── reconciler.py     # Matching engine (ISBN-exact + fuzzy) — COMPLETE (Rec-2)
├── isbn_enricher.py  # Open Library lookup for gap books — COMPLETE (Rec-3)
└── output.py         # All output files (gap CSV, reports) — COMPLETE (Rec-3)
```

**`libib_reader.py` public API** (complete):
```python
LIBIB_EXPORT_HEADERS: list[str]          # the 30-column real export schema

@dataclass
class LibibEntry:
    title: str; creators: str
    tags: set[str]; providers: set[str]  # e.g. {"kindle","kobo"} or {"digital_unknown"}
    ean_isbn13: str; upc_isbn10: str
    skip: bool; ambiguous: bool

def normalize_tags(raw: str) -> set[str]: ...
def classify_providers(tags: set[str]) -> set[str]: ...
def should_skip(tags: set[str]) -> bool: ...
def is_ambiguous(tags: set[str], providers: set[str]) -> bool: ...
def extract_isbns(row: dict[str, str]) -> tuple[str, str]: ...
def read_libib_export(path: str) -> list[LibibEntry]: ...
```
Note `"audiobook"` is a provider keyword that maps to `"chirp"` — real Chirp
entries are commonly tagged `audiobook, chirp` rather than `chirp` alone.

**`reconciler.py` public API** (complete):
```python
ScrapedBook = tuple[str, str, Optional[str], str]   # (title, author, isbn, cover)

@dataclass
class MatchResult:       # one per Libib entry
    entry: LibibEntry; provider: Optional[str]; book: Optional[ScrapedBook]
    confidence: Optional[str]  # "high" | "medium" | "low" | None
    method: Optional[str]      # "exact_isbn" | "fuzzy_title_author" | "title_only" | None
    status: str                # "matched" | "libib_only" | "ambiguous" | "out_of_scope"

@dataclass
class ScrapedBookResult:  # one per scraped book
    provider: str; book: ScrapedBook
    status: str  # "matched" | "missing_from_libib"

@dataclass
class ReconcileResult:
    libib_results: list[MatchResult]
    scraped_results: list[ScrapedBookResult]

def reconcile(
    libib_entries: list[LibibEntry],
    scraped_books: dict[str, list[ScrapedBook]],  # keyed by provider name
) -> ReconcileResult: ...
```

**Matching design** — two-pass, two-pool consumption:
1. **ISBN-exact** (provider-agnostic): scans every unconsumed scraped book
   across *all* providers, not just the entry's tagged ones. This is also the
   only way an `ambiguous` entry (`providers == {"digital_unknown"}`) can ever
   resolve to `matched` — there's no named provider to scope a fuzzy search
   against, so if ISBN doesn't find it, it stays `ambiguous`.
2. **Fuzzy title/author** (provider-scoped): only checked against pools for
   providers actually in `entry.providers`, via `_title_is_plausible()`.
   Candidates are scored with `difflib.SequenceMatcher` and greedily assigned
   highest-score-first — deliberately not an optimal bipartite-matching solver;
   not worth the complexity at personal-library scale. A "stolen" low-confidence
   match is exactly what a future low-confidence report (Rec-3) is for.
   Confidence is `medium` if the scraped book's author shares a word with the
   Libib entry's `creators` field, else `low`.
3. Once a scraped book is consumed by any match, it's removed from further
   consideration — no double-booking one book across two Libib entries.

**Dedup gotcha (real bug found and fixed in Rec-2):** `_dedupe_scraped_books()`
reuses `lib.dedupe_books_by_title()`, which only understands 3-tuples. The first
implementation reattached `cover` after dedup via a `{isbn: cover}` dict — which
silently collided and dropped covers whenever multiple scraped books shared an
empty/unresolved ISBN (very common pre-enrichment). Fixed by packing
`isbn` + `cover` together into the function's opaque 3rd-field slot (which
`dedupe_books_by_title` never inspects, only passes through) and unpacking
after — lossless regardless of how many books share a missing ISBN. See the
regression test `test_dedupe_scraped_books_helper_preserves_cover_when_isbn_missing_for_many`
in `tests/test_reconcile.py`.

**`isbn_enricher.py` public API** (complete):
```python
ISBN_LOG_INTERVAL: int = 25

def enrich_missing_isbns(result: ReconcileResult) -> ReconcileResult: ...
```
Resolves ISBNs only for `scraped_results` entries with `status ==
"missing_from_libib"` and no ISBN yet — books that already have one (Nook's DOM,
a future Google response) are never re-looked-up. `libib_results` pass through
unchanged. Logs progress/rate the same way every scraper's own `resolve_isbns()`
does, rather than returning a separate stats object.

**`output.py` public API** (complete):
```python
def enrich_gap_books(
    gap_books: list[ScrapedBookResult], no_enrich: bool = False
) -> list[tuple[ScrapedBookResult, EnrichmentResult]]: ...

def write_gap_csv(
    enriched_gap_books: list[tuple[ScrapedBookResult, EnrichmentResult]],
    output_dir: str, timestamp: str,
) -> str: ...

def write_reconciliation_report(result: ReconcileResult, output_dir: str, timestamp: str) -> str: ...
def write_orphan_report(result: ReconcileResult, output_dir: str, timestamp: str) -> Optional[str]: ...
def write_low_confidence_report(result: ReconcileResult, output_dir: str, timestamp: str) -> Optional[str]: ...
def write_ambiguous_report(result: ReconcileResult, output_dir: str, timestamp: str) -> Optional[str]: ...
```
Every writer takes an already-computed `timestamp` string rather than generating
its own — a single reconcile run's files share one timestamp (`reconcile_<ts>_gap.csv`,
`_summary.txt`, `_orphans.txt`, `_low_confidence.txt`, `_ambiguous.txt`), and each
writer stays independently testable without freezing the clock. The four report
writers return `None` (write nothing) when there's nothing to report; `write_gap_csv`
and `write_reconciliation_report` always write a file, even if empty/all-zero, matching
the scraper `write_csv()` convention of always producing a CSV. `_PROVIDER_TAGS` in
`output.py` duplicates each scraper's `LIBIB_TYPE` constant as static data rather than
importing from scraper internals — the reconciler package doesn't depend on the
scraper packages for anything but the shared `lib/`.

**`core.py` public API** (complete):
```python
@dataclass
class ReconcileRunResult:
    gap_csv_path: Optional[str]; summary_path: Optional[str]
    orphan_path: Optional[str]; low_confidence_path: Optional[str]
    ambiguous_path: Optional[str]
    total_libib_entries: int; total_gap_books: int

def run(
    *, libib_path: str, output_dir: str = ".", providers: Optional[list[str]] = None,
    scrape: bool = False, chirp=None, kindle=None, kobo=None, nook=None, google=None,
    dry_run: bool = False, no_enrich: bool = False,
) -> ReconcileRunResult: ...
```
Follows the same `run()`/`main()` split as the four scrapers (Rec-4a).
Orchestration order: `read_libib_export()` → `_gather_scraped_books()` →
`reconcile()` → `enrich_missing_isbns()` → (if not `dry_run`)
`write_reconciliation_report()` → `enrich_gap_books()` → `write_gap_csv()` →
`write_orphan_report()`/`write_low_confidence_report()`/`write_ambiguous_report()`,
all sharing one `timestamp` generated once in `run()`.

`reconciler.py` reuses `LibibEntry.providers`/`.ean_isbn13`/`.upc_isbn10` directly
— `libib_reader.py` already does the tag-classification work, so the matcher
never touches raw tags. `isbn_enricher.py`/`output.py` consume `ReconcileResult`
directly: gap CSV = `scraped_results` where `status == "missing_from_libib"`
(ISBN-enriched first via `enrich_missing_isbns()`, then metadata-enriched via
`enrich_gap_books()`); orphan report = `libib_results` where
`status == "libib_only"`; low-confidence report = `libib_results` where
`confidence in ("medium", "low")`; ambiguous report = `libib_results` where
`status == "ambiguous"`.

**`--scrape` / pre-existing-CSV design — three decisions worth remembering:**
1. **CSV is the interchange format, not an in-memory return.** Scraper `run()`
   (Rec-4a) only returns `RunResult` (paths + counts), never book tuples. So
   `--scrape` calls a scraper's `run()`, takes the `csv_path` it wrote, and
   immediately reads it back via `_load_scrape_csv()` — the exact same function
   that parses a user-supplied `--kindle PATH` file. One code path for both.
2. **`--scrape` always passes `no_enrich=True` to the scraper.** Enriching
   every scraped book up front is wasted work — most of them are already in
   Libib and get discarded by matching; only the gap subset needs enrichment,
   which `enrich_gap_books()` does after matching narrows the set down. This
   also sidesteps a real correctness trap: with enrichment on, a scraper's
   `notes` column may already contain a `"Series: X #009 || Additional Notes:
   <cover>"` prefix, and `_load_scrape_csv()` has no reliable way to strip that
   back out to recover the raw cover URL — `no_enrich=True` keeps `notes`
   exactly the raw cover URL, always.
3. **`--dry-run` does not suppress `--scrape`'s intermediate CSVs.** `run()`'s
   `dry_run` flag only gates `libib_reconcile`'s *own* five output files
   (summary, gap CSV, three reports). If `--scrape` is set, each provider
   scrape still runs for real and writes its CSV to `--output-dir` regardless
   — there's no way to gather scrape data without it under the current
   scraper `run()` contract, and inventing one just for a dry-run edge case
   would be over-engineering. Documented directly in `run()`'s docstring.

**`pyproject.toml`** (current):
```toml
[tool.setuptools]
packages = [
    "chirp_to_libib", "kindle_to_libib", "kobo_to_libib",
    "nook_to_libib", "libib_reconcile", "lib",
]

[tool.coverage.run]
source = ["chirp_to_libib", "nook_to_libib", "libib_reconcile", "webapp"]
```
`google_to_libib` joins `packages` once Google-1 ships. `kindle_to_libib` and
`kobo_to_libib` are notably **not** in `coverage.run.source` — a pre-existing
gap from before this session, left as-is (out of scope for the reconciler work).

---

## The `webapp` Module (GUI-Backend-1..5, GUI-Frontend-1..3 COMPLETE, 2026-07-22)

Local web GUI over all four scrapers, built exactly to the plan in
`docs/backlog.md`'s "Web GUI (webapp)" section — FastAPI + Jinja2 + one shared
`static/style.css`, no SPA framework, no build step. `python -m webapp` runs it
at `http://127.0.0.1:8000` (binding hardcoded, never configurable to `0.0.0.0`).

**Why a thread per job, not asyncio:** Selenium's Python bindings are
synchronous end-to-end — no `await`-able API. Running a scrape inside an
`async def` route body would block the whole event loop, including the SSE
stream telling the browser what's happening. `webapp/jobs/runner.py` spawns a
real daemon `threading.Thread` per job; this is a legitimate use of threading
specifically because the work is blocking I/O behind a third-party sync
library, not a case where asyncio would've been the natural fit.

**Job lifecycle:** `queued → (waiting_for_login →)? running →
completed/failed/cancelled`. `webapp/jobs/registry.py`'s `Job` dataclass holds
`log_queue`, `continue_event`, `cancel_event`, `thread`, `result` (the whole
`RunResult`/`ReconcileRunResult` object a scraper's `run()` returned — not a
separately-extracted `result_paths` dict, see below), `error`, and
`output_dir` (resolved to an absolute path by `scrape_job_start()`, skipped
for dry runs — lets the GUI tell the user exactly where files landed instead
of only offering a Download button).
`JobRegistry` enforces **one active job per provider** — not just for UI
clarity, running two concurrent sessions against a CAPTCHA-sensitive site
(Kobo/Chirp/Nook) from the same browser profile is a real way to get flagged.

**The `wait_fn`/`cancel_fn` injection pattern** (built on Rec-4a's scraper
refactor): `webapp/jobs/runner.py`'s `_make_wait_fn(job)` builds a function
that flips the job to `waiting_for_login` and polls `job.continue_event` (set
by a `POST .../continue`, checked every 0.2s against `job.cancel_event` too)
until it fires; `_make_cancel_fn(job)` just returns `job.cancel_event.is_set`.
`start_job()`'s `run_callable` signature is `(wait_fn, cancel_fn)`.
`_build_run_callable()` in `webapp/app.py` uses `inspect.signature(module.run)`
to detect whether the target scraper's `run()` accepts a `wait_fn`/`cancel_fn`
param at all (Kindle's `run()` has no `wait_fn` — automated login) rather than
hardcoding per-provider branches — this also means a future fifth scraper
without `cancel_fn` wouldn't break.

**Cancellation is cooperative, not a hard interrupt (`GUI-BACKEND-4a`,
2026-07-23).** `job.cancel_event` is observed in two places: the login-wait
poll loop (`_make_wait_fn`), and — since `GUI-BACKEND-4a` — a
`_make_cancel_fn(job)` checker threaded through every scraper's `run()` as
`cancel_fn: Callable[[], bool]`, checked between scrape pages, between each
book's ISBN lookup/enrichment call, and between individual HTTP retry
attempts inside `lib/http_retry.py`'s `request_json()`. Raises the shared
`lib.cancellation.OperationCancelled`, caught in `runner.py`'s `_run()`. This
still doesn't interrupt a **live Selenium call already in flight** — that
needs a driver reference plumbed out of each scraper's `scrape_*()`, tracked
as the still-deferred Tier-2 "force stop" follow-up in `docs/backlog.md`. The
FastAPI shutdown hook (a `lifespan` context manager) and the `POST /shutdown`
route both set `cancel_event` the same way, so they share this scope too.

**Log bridge:** one `JobLogHandler` installed on the **root logger** at
`webapp/app.py` import time. `register_thread(job)`/`unregister_thread()`
(called from `runner.py`'s `_run()`) populate a `{thread_id: Job}` map;
`emit()` looks up the emitting thread and pushes matching records onto that
job's queue. Every scraper's existing `log.info(...)` calls reach the right
job's log stream with **zero changes to any scraper module**. Gotcha for
future tests: because the handler is on the *root* logger, any test logger
with default propagation enabled will get double-delivered into a job's queue
if that thread happens to be registered — set `logger.propagate = False` in
tests that create their own `JobLogHandler` (see `tests/test_log_bridge.py`).

**A real bug found here (2026-07-23), worth remembering:** `install()` must
call `root.setLevel(logging.INFO)` itself, not just `addHandler()`. Each
scraper module's own `logging.basicConfig(level=logging.INFO, ...)` (its
CLI-path setup) is a complete no-op once the root logger already has a
handler attached — Python's `basicConfig()` skips everything it does,
including setting the level, whenever `root.handlers` is non-empty (unless
`force=True`). Since `install()` runs at webapp startup, before any scraper
module is ever imported, every scraper's `basicConfig()` call became a
no-op under the GUI and the root logger silently stayed at Python's
`WARNING` default — every `log.info(...)` progress line (all per-page/
per-book output) was dropped before it ever reached the handler, with zero
error or indication why. Only warnings/errors ever showed up in the GUI's
log panel until this was fixed. The CLI path never hit this, since there a
scraper's own `basicConfig()` is the first thing to touch the root logger.

**SSE, not WebSocket:** the browser only needs one-directional push (log
lines + status changes); Continue/Cancel are independent, infrequent, and map
cleanly onto ordinary `POST` requests. `_job_event_stream()` in `webapp/app.py`
emits a named `event: status` every time `job.status` changes (so the page
knows to show/hide the Continue button and update the badge without polling),
unnamed `data:` lines for log output, and a final `event: done` once the job
is terminal *and* the log queue is empty — never before, so buffered log
lines are never dropped.

**Route inventory** (all in `webapp/app.py`, one `create_app()` factory —
no router-splitting yet, revisit if it gets unwieldy):

| Route | Purpose |
|---|---|
| `GET /` | Dashboard — tool cards + live status badges (`_latest_status_per_provider()`) |
| `GET /api/jobs-live` | Whether any job is queued/waiting/running — backs the Quit button's confirm dialog |
| `POST /shutdown` | Cancels any live jobs, then `os._exit(0)` after a short delay so the response reaches the browser first |
| `GET /scrape/{provider}` | Run-a-scraper page; 404 for unknown/disabled (Google) |
| `POST /scrape/{provider}/jobs` | Start a job (`ScrapeOptions` body); 404 unknown provider, 409 already running |
| `GET /scrape/{provider}/jobs/{id}/events` | SSE log/status stream |
| `GET /scrape/{provider}/jobs/{id}` | JSON job detail: status, error, `output_dir`, download links |
| `POST /scrape/{provider}/jobs/{id}/continue` | Unblocks the login wait; 409 if not `waiting_for_login` |
| `POST /scrape/{provider}/jobs/{id}/cancel` | Cooperative cancel (login wait, and — since `GUI-BACKEND-4a` — between scrape pages/books/retries); 409 if already terminal |
| `GET /downloads/{job_id}/{filename}` | Serves a file — **only** if `filename` matches one of the job's own known result paths by basename; never joins user input onto a directory |

**Security implemented so far:** `127.0.0.1`-only binding; downloads matched
by basename against the job's own result object, never a raw path join from
`filename` (`_extract_result_paths()` pulls string fields off the
`RunResult`/`ReconcileRunResult` dataclass generically). **Not yet done:**
Origin/Referer CSRF check on state-changing routes, full path-traversal test
sweep, credential-handling review — all `GUI-Security-1`, not started. Note
`POST /shutdown` is a state-changing route with the same gap as every other
POST route today — worth prioritizing in `GUI-Security-1` given its blast
radius (kills the whole server) is bigger than `/cancel`'s.

**Testing gotcha worth remembering:** `@patch("webapp.app._SCRAPER_MODULES",
...)` combined with `@patch("webapp.app.importlib.import_module")` in the same
test silently breaks the first patch. Worked around in
`tests/test_webapp_scrape_dispatch.py` by not patching `_SCRAPER_MODULES` at
all — since `import_module` is already mocked, it returns the fake module
regardless of which real module name string gets looked up.

**Not automated-tested:** `webapp/static/app.js`. No JS test runner exists in
this Python-only test stack, and adding one (Node/Jest) for ~130 lines of
vanilla JS would be disproportionate. The Python side it depends on (page
route, job-detail endpoint, SSE status events) has full coverage.
**Manually verified end-to-end, 2026-07-23:** the user ran all four scrapers
for real through the GUI (real manual logins, real Continue clicks, real SSE
log streaming) across several rounds of live bug reports — this is what
surfaced the quit button, the login-wait popup bug, the Google Books
rate-limiting chain, the Kindle credentials hang, and the root-logger level
bug documented above. No longer an open action item.

---

## Session History

| Session | Date | What was built |
|---------|------|----------------|
| Pre-backlog | 2026-06-09 | chirp_to_libib, kindle_to_libib, kobo_to_libib complete; shared lib; 48+ tests passing; CI green; README complete |
| Backlog planning | 2026-06-09 | Analyzed real Libib export; designed libib_reconcile + nook + google scrapers; created docs/ |
| Enrich-planning | 2026-06-10 | Designed metadata enrichment epic; updated backlog + CLAUDE.md; enricher not yet coded |
| Enrich-1 | 2026-07-21 | lib/enricher.py — EnrichmentResult, enrich_book, format_series_notes; Open Library + Google Books metadata; Wikidata series; 33 tests in test_enricher.py |
| Enrich-2 | 2026-07-21 | Wired enrich_books() into Chirp/Kindle/Kobo main() pipelines; --no-enrich flag on all three; updated test_output.py/test_kobo.py/test_pipeline.py/test_cli.py; README enrichment section |
| AI-1 | 2026-07-21 | Optional OpenAI metadata fallback in lib/enricher.py, gated by AI_PROVIDER/OPENAI_API_KEY env vars; never touches series/group resolution |
| Nook-1 | 2026-07-21 | nook_to_libib core scraper — DOM confirmed same day; ISBN-from-DOM pipeline, enrichment wired in from scaffold (ENR-14) |
| Nook-2 | 2026-07-21 | nook_to_libib tests (test_nook.py, 21 tests), pyproject.toml packages/coverage, README, docs/CLAUDE.md; libib_reconcile provider-tag update deferred (module not scaffolded yet) |
| Google-1 | TBD | google_to_libib OAuth + API client + CSV output |
| Google-2 | TBD | google_to_libib tests, CI, README |
| Rec-1 | 2026-07-21 | libib_reconcile scaffolding + libib_reader.py (36 tests); discovered the export-vs-import schema mismatch; `.gitignore` exception added for `tests/fixtures/*.csv` |
| GUI-planning | 2026-07-21 | Designed reconciler backend (Rec-2..Rec-5, Rec-4a refactor) + local web GUI (`webapp/`, FastAPI/Jinja2/SSE) architecture as a portfolio-quality addition; full plan written into `docs/backlog.md` |
| Rec-2 | 2026-07-22 | libib_reconcile reconciler.py (matching engine, 22 tests); found + fixed a real cover-URL-dropping dedup bug (isbn-keyed dict collision on empty ISBNs) |
| Rec-3 | 2026-07-22 | libib_reconcile isbn_enricher.py + output.py (gap CSV + 4 reports); 22 tests |
| Rec-4a | 2026-07-22 | Extracted `run()`/`RunResult` from `main()` in all four scrapers; added `wait_fn` to Chirp/Kobo/Nook `_login()`/`_default_wait()`; 13 new `run()` tests; only 1 pre-existing test needed a change (a mock call-args assertion learning about the new `wait_fn` kwarg) |
| Rec-4 | 2026-07-22 | libib_reconcile core.py CLI/orchestration (`run()`/`main()`, `--scrape`, per-provider paths, `--providers`, `--dry-run`, `--no-enrich`); 16 tests incl. a real end-to-end integration test; added to coverage source; `python -m libib_reconcile` verified working end-to-end |
| Rec-5-lite | 2026-07-22 | Parser-only validation against the real 7026-*line*/3461-*row* export (`wc -l` counts raw newlines inside quoted description fields, not CSV rows — don't be alarmed by the mismatch); zero errors, correct provider counts, zero unclassified active entries. Full Rec-5 (real scrapes, threshold tuning) still needs a human present for live browser logins. |
| GUI-Backend-1 | 2026-07-22 | `webapp/` skeleton — app factory, `127.0.0.1` binding, dashboard w/ tool cards + disabled Google placeholder; new deps (fastapi/uvicorn/jinja2/python-multipart/python-dotenv/httpx) |
| GUI-Backend-2 | 2026-07-22 | Job registry + runner — `Job`/`JobRegistry`/`start_job()`, one-active-job-per-provider, thread-based (Selenium has no async API); 8 tests against a fake provider, no Selenium |
| GUI-Backend-3 | 2026-07-22 | Log bridge (root-logger handler, thread-id→job map) + SSE `/events` endpoint; caught a real cross-test double-delivery interaction (root handler + test logger propagation sharing global `_thread_jobs` state) |
| GUI-Backend-4 | 2026-07-22 | Continue/cancel endpoints + `lifespan` shutdown hook — **Tier 1 only** (interrupts login-wait, not mid-scrape); Tier 2 tracked as follow-up `GUI-BACKEND-4a`, not built |
| GUI-Backend-5 | 2026-07-22 | Scraper dispatch (`POST /scrape/{provider}/jobs`, signature-aware `wait_fn` injection) + safe downloads (basename-matched against the job's own result, never a raw path join); found a `mock.patch` interaction bug (patching `_SCRAPER_MODULES` + `importlib.import_module` together silently breaks the first patch) |
| GUI-Frontend-1 | 2026-07-22 | Full `style.css` design system (tokens, dark mode, `.card`/`.btn`/`.badge`/`.log-panel`/forms) + `base.html` layout |
| GUI-Frontend-2 | 2026-07-22 | Dashboard status badges (`_latest_status_per_provider()` — caught and fixed a real `datetime.now()` timestamp-tie bug via `>=` instead of `>`). Reconcile card still deferred to `GUI-Reconcile-1`. |
| GUI-Frontend-3 | 2026-07-22 | Run-a-scraper page (`scrape.html` + `app.js`) — form, live log via SSE, login-wait UI, download links. Added `event: status` SSE events and a job-detail JSON endpoint beyond the original ticket scope, since the original design (only a final `done` event) couldn't support a live Continue button. `app.js` itself unverified in a real browser at the time — closed out in the GUI-live-testing session below. |
| GUI-Backend-4a | 2026-07-23 | Cooperative cancel (checked between scrape pages/books/HTTP retries, not just login-wait) across all four scrapers; `lib/http_retry.py` (centralized retry helper, real 429/`Retry-After` handling — found via manual testing that Google Books' 429s weren't being backed off long enough); `POST /shutdown` + `GET /api/jobs-live` + a Quit button; fixed a real UX bug where the login-wait Continue button looked unresponsive for ~1s; `Job.output_dir` + a "Files saved to" note alongside the download buttons |
| GUI-live-testing | 2026-07-23 | First real end-to-end browser testing of `app.js`/all four scrapers, driven entirely by live user bug reports rather than a written test plan. Chain of enrichment-reliability fixes, each one prompted by the previous fix not being enough: wider jitter → didn't help (Google's anonymous quota doesn't clear in seconds) → circuit breaker (skip Google Books for a cooldown after one 429 instead of re-discovering the same block every book) → optional `GOOGLE_BOOKS_API_KEY` (the actual root-cause fix — bigger separate quota) → discovered and fixed `AI_PROVIDER` case-sensitivity and a total absence of AI-fallback success logging. Then a `.env`/webdriver_manager credentials hang (Kindle blocking the whole server on a stdin prompt the browser can't answer) → `credentials_from_env()` non-interactive variant. Then, after adding per-book progress logging still didn't produce visible output, traced it to the session's most significant find: **the root logger's level was silently stuck at WARNING under the GUI the entire time** (each scraper's `logging.basicConfig(level=logging.INFO, ...)` no-ops once the root logger already has a handler — which `log_bridge.install()` always attaches first, at webapp startup) — every `log.info(...)` progress line, across every prior GUI session, had been silently dropped; fixed with one `root.setLevel(logging.INFO)` line, confirmed by reproducing the broken behavior standalone before shipping the fix. Also: `[hidden]` CSS bug (a `.card { display: flex }` rule beat the browser's default `[hidden] { display: none }` regardless of specificity, so the login-wait card never actually hid), Cancel-button/status-row layout fixes, and a `.env` + `GOOGLE_BOOKS_API_KEY`/`credentials_from_env` setup walkthrough. |
| Rec-5 | Next up | Integration run against real data. **Not yet done — this is the very next step.** User is about to run a full scrape of their real collection across all four providers (validated working end-to-end as of the session above) specifically to generate the real scrape CSVs this needs; also grab a fresh Libib export first (the committed reference file is from 2026-06-09, stale). Then `python -m libib_reconcile --libib <fresh-export.csv> --chirp ... --kindle ... --kobo ... --nook ...` (or `--scrape` if driving scrapers directly from the reconciler CLI) against the real data — matching quality/fuzzy-threshold tuning can't be meaningfully validated on small samples. |
| GUI-Reconcile-1..3 | TBD | Libib CSV upload, reconcile job wiring, results page |
| GUI-Settings-1 / GUI-Security-1 / GUI-Polish-1 | TBD | Settings page, CSRF/path-traversal hardening, docs |

---

## Key Decisions & Lessons Learned

- **The `run()`/`wait_fn` refactor paid off exactly as planned** — Rec-4a was
  done for the reconciler's own `--scrape` benefit, but `webapp`'s job runner
  reused the identical pattern with zero changes to any scraper: `run_callable
  = lambda wait_fn: module.run(wait_fn=wait_fn, ...)`, with
  `inspect.signature()` detecting whether a given scraper even accepts
  `wait_fn` (Kindle doesn't). This is the payoff case for "do the refactor
  once, for the immediate consumer's benefit, and the next consumer inherits
  it for free" — worth remembering as a pattern, not just a one-off.
- **Shared global test state bites twice in `webapp`'s test suite** — the
  job registry (`webapp.app.registry`) and the log bridge's `_thread_jobs`
  map are process-wide singletons, imported once and shared across every test
  module in the session. Two real bugs came from this: (1) a test's own
  `JobLogHandler` double-received log lines via the root-logger handler
  installed by importing `webapp.app` (fix: `logger.propagate = False` in
  tests); (2) a dashboard test that created a "chirp" job and left it
  `queued` blocked a later test module's `/scrape/chirp/jobs` dispatch call
  (fix: always leave test-created jobs in a terminal status before the test
  ends). When adding new `webapp` tests, assume other test files' jobs are
  sitting in the same registry and either use a unique provider slug or
  clean up to a terminal status.
- **`run()`/`main()` split + injectable `wait_fn`, not a bigger refactor** — when
  extracting callable entry points from each scraper's `main()` (Rec-4a), the
  temptation is to also centralize the duplicated `run()`/`RunResult`/`_default_wait`
  boilerplate into `lib/`. Deliberately didn't: each scraper's `_default_wait()`
  carries scraper-specific instructional text, and `RunResult` is small enough
  (4 fields) that a shared base class would cost more in indirection than it
  saves in duplication. `wait_fn: Callable[[], None] = _default_wait` is the one
  piece of real shared *shape* — Chirp/Kobo/Nook all use it identically — and
  that's carried by convention (same pattern copied four times, matches the tuple
  pipeline's whole design philosophy), not a shared abstraction. Revisit only if
  a fifth manual-login scraper (unlikely — Google Books is OAuth, no manual login)
  makes the duplication cost clearly outweigh the abstraction cost.
- **Libib export schema ≠ import schema** — discovered 2026-07-21 while starting
  the reconciler. The real export CSV has `length`/`began`/`completed` and several
  export-only columns (`item_type`, `first_name`, `last_name`, `collection`,
  `number_of_players`, `age_group`, `ensemble`, `esrb`), none of which match
  `lib.LIBIB_HEADERS` (`length_of`/`began_date`/`completed_date`, no equivalents
  for the export-only columns). `libib_reconcile/libib_reader.py` has its own
  `LIBIB_EXPORT_HEADERS` constant for this reason — never assume the two schemas
  are interchangeable when touching reconciler code.
- **Test fixtures over the real export file** — the 7026-row real Libib export
  is gitignored/local-only and too large to review; reconciler tests use a small
  hand-crafted `tests/fixtures/libib_export_sample.csv` (15 rows, one per tag/edge
  case) instead. This required a `.gitignore` exception (`!tests/fixtures/*.csv`)
  since the blanket `*.csv` rule (personal library data) would otherwise silently
  exclude committed test fixtures too — watch for this if adding more CSV fixtures.
- **Backend before GUI, deliberately** — the reconciler (`libib_reconcile`) is
  being finished and fully tested before any GUI work starts, per explicit user
  preference: avoids rework and lets the GUI's job-runner design build on a
  known-working `run()` entry point (see `Rec-4a` in `docs/backlog.md`) rather
  than an evolving one.
- **This is a public community tool** — design for other users, not just Jennifer's
  personal setup. Document the OAuth setup for Google clearly. Manual login flows
  must have clear terminal instructions. — design for other users, not just Jennifer's
  personal setup. Document the OAuth setup for Google clearly. Manual login flows
  must have clear terminal instructions.
- **Chirp, Kobo, Nook — manual login is correct** — bot detection on all three is
  robust. Do not attempt to automate. The manual pause approach is the stable solution.
- **Kobo two-tab trick** — Tab 1 opened by Selenium gets fingerprinted by hCaptcha.
  User manually opens tab 2 (copies URL from address bar → Ctrl+T → paste). Script
  switches to `window_handles[-1]`.
- **Kindle scraper is working fine** — `kindle_to_libib` scrapes the Amazon web
  library page metadata, not the book files. Any DRM/epub tooling (e.g. Epubor) is
  a separate personal workflow unrelated to LibibTools and does not affect the scraper.
- **Google Books API returns ISBNs directly** — skip Open Library lookup for books
  that already have an ISBN from the API. Only fall back to Open Library if neither
  ISBN-13 nor ISBN-10 is present in `industryIdentifiers`.
- **Google Books API also returns metadata** — description, publisher, publishedDate,
  pageCount are in the volume response. Pass these through to the enricher so it
  skips redundant Open Library + Google Books metadata calls for Google books.
- **GoodReads has no public API** — it shut down developer access in 2020 and actively
  blocks scraping. Never use GoodReads as a data source in LibibTools.
- **Wikidata is the series source** — free, stable, ToS-safe SPARQL API. Coverage is
  best for well-known genre series (fantasy, sci-fi). Use ISBN-13 (`wdt:P212`) as
  primary lookup key; fall back to title+author.
- **LLMs must not be used for series order** — hallucination risk is too high. A
  confident wrong answer is worse than `#ZZZ`. Use `#ZZZ` to flag unknown position
  so users can find and fix it manually.
- **AI provider fallback is scoped to general metadata only** (description, publisher,
  publish_date, length_of) — added as a third fallback after Open Library and Google
  Books, before falling to blank. It never touches series/`group` resolution, which
  remains Wikidata-only. Opt-in via `AI_PROVIDER` env var (e.g. `openai`) plus a
  provider-specific key (`OPENAI_API_KEY`); if unset, this stage is a silent no-op.
  Built as a provider-agnostic interface so other providers can be added later. See
  AI-1 in `docs/backlog.md`.
- **Series position format** — `#009` (hash + 3-digit zero-padded). Notes prefix:
  `Series: <Name> #<pos> || Additional Notes: <original>`. Community tool needs to
  handle series with 100+ entries.
- **Price is not fetched** — no free API has reliable coverage. Preserve existing
  price data if present; otherwise leave blank.
- **`--no-enrich` flag on all scrapers** — enrichment makes HTTP calls per book and
  takes time. Users need an escape hatch for fast/offline runs.
- **`filesystem:write_file` over `filesystem:edit_file`** — full rewrites are more
  reliable when file state may have drifted between reads.
- **Black is strict** — run Black on any new file before considering it done. Even
  one extra blank line will fail CI.
- **Libib API has no items endpoint** — REST API covers only accounts/managers/patrons.
  Items require manual CSV export from Settings.
- **Tuple data model** — all scrapers use plain tuples, not dataclasses. Keep this
  consistent in `nook_to_libib`, `google_to_libib`, and `libib_reconcile`.
  `EnrichmentResult` is the one dataclass exception, defined in `lib/enricher.py`.
- **Import from `lib`, not `lib.openlibrary` or `lib.enricher`** — `lib/__init__.py`
  re-exports everything cleanly. All new code must follow this pattern.
- **Selenium sandbox limitation** — the sandbox does not have Selenium installed.
  Tests for browser logic must mock `By`, `WebDriverWait`, and `EC` via
  `patch.dict("module.__dict__", ...)` rather than patching as attributes.
- **Nook DOM selectors are confirmed** (2026-07-21) — see [Nook Scraper — Key Notes](#nook-scraper--key-notes).
  The old "screenshot needed before Nook-1" caveat no longer applies.
- **Nook's ISBN comes from the DOM, not a lookup** — the `data-test` attribute on each
  book's `<li>` is the ISBN-13 itself. `resolve_isbns()` in `nook_to_libib` is a
  fallback path, not the primary source, unlike every other scraper. Don't assume
  Nook's pipeline mirrors Chirp/Kindle/Kobo's when reasoning about it.
- **Nook's dedupe/filter reuses the shared 3-tuple helpers via an ISBN-keyed swap** —
  `filter_invalid_books()`/`dedupe_books_by_title()` are typed for `(title, author,
  cover)`. Since Nook's 4-tuple has an extra `isbn` field, `_dedupe_and_filter()` in
  `nook_to_libib/core.py` swaps `isbn` into the "cover" slot for that pass (ISBN is a
  more reliable identity key than title anyway), then re-attaches the real cover
  afterward by ISBN. This was a deliberate choice to reuse validated shared logic
  rather than duplicating filter/dedupe for one scraper's edge case.
