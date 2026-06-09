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
├── nook_to_libib/         # Barnes & Noble Nook scraper (PLANNED)
├── google_to_libib/       # Google Play Books scraper (PLANNED)
├── lib/                   # Shared logic (all scrapers import from here)
│   ├── __init__.py
│   └── openlibrary.py
├── libib_reconcile/       # Reconciliation tool (PLANNED)
├── tests/
│   ├── conftest.py
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
| Nook | `nook_to_libib` | 🔲 Planned | Selenium + manual login | `nook,ebook` |
| Google Books | `google_to_libib` | 🔲 Planned | Google Books API + OAuth 2.0 | `google,ebook` |

---

## Shared Library (`lib/`)

All scrapers import exclusively from `lib/`. Never import from `lib.openlibrary`
directly in new code — always use `from lib import ...`.

### Key exports from `lib/`

| Symbol | Description |
|--------|-------------|
| `LIBIB_HEADERS` | Ordered list of all 28 Libib CSV column names |
| `classify_identifier(isbn)` | Returns `(upc_isbn10, ean_isbn13)` tuple |
| `get_isbn(title, author)` | Open Library ISBN lookup with retry/fallback |
| `sleep_between_requests()` | Randomized delay (0.8–1.6s) for rate limiting |
| `dedupe_books_by_title(books)` | Remove duplicate `(title, author, cover)` tuples |
| `filter_invalid_books(books)` | Drop empty/garbage titles |

### Internal helpers in `lib/openlibrary.py` (not exported, but available for reuse)

| Symbol | Description |
|--------|-------------|
| `_title_is_plausible(query, returned, threshold)` | Fuzzy title match — **reuse in reconciler** |
| `_valid_isbn13(s)` | Validates ISBN-13 checksum |
| `_valid_isbn10(s)` | Validates ISBN-10 checksum |
| `_best_isbn(isbns)` | Picks best ISBN from a list (prefers ISBN-13) |

---

## Scraper Architecture

### Selenium scrapers (Chirp, Kindle, Kobo, Nook)

All follow the identical pipeline:

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
write_csv()  [28-column Libib CSV, UTF-8-sig]
write_unresolved()  [txt report of books with no ISBN]
```

### Google Books scraper (API-based, no Selenium)

```
_load_credentials() → _authorize() → fetch_all_books()
    ↓
list of (title, author, isbn, cover_url) tuples
    [ISBNs come directly from API — Open Library lookup only for books with no ISBN]
    ↓
filter_invalid_books()
    ↓
dedupe_books_by_title()
    ↓
resolve_isbns()  [skips books that already have an ISBN from the API]
    ↓
write_csv() / write_unresolved()
```

### Data model
All scrapers use plain tuples — **no dataclasses or ORM**:
- Scrape output: `list[tuple[str, str, str]]` → `(title, author, cover_url)`
- After ISBN resolution: `list[tuple[str, str, Optional[str], str]]` → `(title, author, isbn, cover_url)`
- Google Books may produce `(title, author, isbn, cover_url)` directly from the API

### Login strategies

| Scraper | Strategy |
|---------|----------|
| Chirp | Manual login pause — bot detection blocks automation |
| Kindle | Automated login (email/password via env vars or prompt) |
| Kobo | Manual two-tab login — hCaptcha blocks Selenium tab 1; user opens tab 2 by copying URL from address bar |
| Nook | Manual login pause — B&N Akamai bot detection; same approach as Chirp |
| Google Books | No browser login — OAuth 2.0 consent flow on first run; token auto-refreshes |

---

## Nook Scraper — Key Notes

- Library URL: `https://www.barnesandnoble.com/account/my-digital-library`
- B&N uses **Akamai** bot detection — manual login is correct from the start
- DOM selectors are **unknown** until a DevTools screenshot is provided
- **Do not write `_parse_items()` without the screenshot** — do not guess selectors
- Follow the exact same module structure as `kobo_to_libib`

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
- Pagination: `startIndex` + `maxResults=40`; loop until `totalItems` exhausted
- Rate limit: 1000 requests/day (free tier) — no issue for personal libraries
- **No `--pages` flag needed** — the API returns the full library; just paginate to completion
- The one-time Cloud Console setup (enable Books API, create OAuth credentials) must be
  documented clearly in the README for community users

---

## Libib CSV Schema

The export and import schema share the same 28 columns (`LIBIB_HEADERS`):

```
added, creators, began_date, call_numbers, completed_date, copies,
description, group, upc_isbn10, ean_isbn13, ddc, lcc, lccn, oclc,
lexile, length_of, number_of_discs, aspect_ratio, notes, price,
publish_date, publisher, rating, review, review_date, status, tags, title
```

Scrapers populate only: `title`, `creators`, `upc_isbn10`, `ean_isbn13`, `tags`, `notes` (cover URL).

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

## The `libib_reconcile` Module (PLANNED)

See `docs/backlog.md` for the full backlog. High-level summary:

**Purpose:** Compare a Libib export CSV against scrapes from all five providers,
identify missing books, enrich with ISBNs, and produce a ready-to-import gap CSV
plus human-readable reports.

**Planned file structure:**
```
libib_reconcile/
├── __init__.py
├── __main__.py
├── core.py           # Orchestration / main pipeline
├── libib_reader.py   # Parse and classify Libib export CSV
├── reconciler.py     # Matching engine (ISBN-exact + fuzzy)
├── isbn_enricher.py  # Open Library lookup for gap books
└── output.py         # All output files (gap CSV, reports)
```

**`pyproject.toml` when scaffolded:**
```toml
[tool.setuptools]
packages = [
    "chirp_to_libib", "kindle_to_libib", "kobo_to_libib",
    "nook_to_libib", "google_to_libib", "lib", "libib_reconcile"
]
```

---

## Session History

| Session | Date | What was built |
|---------|------|----------------|
| Pre-backlog | 2026-06-09 | chirp_to_libib, kindle_to_libib, kobo_to_libib complete; shared lib; 48+ tests passing; CI green; README complete |
| Backlog planning | 2026-06-09 | Analyzed real Libib export; designed libib_reconcile + nook + google scrapers; created docs/ |
| Nook-1 | TBD | nook_to_libib core scraper (needs DOM screenshot first) |
| Nook-2 | TBD | nook_to_libib tests, CI, README |
| Google-1 | TBD | google_to_libib OAuth + API client + CSV output |
| Google-2 | TBD | google_to_libib tests, CI, README |
| Rec-1 | TBD | libib_reconcile scaffolding + libib_reader.py |
| Rec-2 | TBD | libib_reconcile reconciler (matching engine) |
| Rec-3 | TBD | libib_reconcile ISBN enrichment + output files |
| Rec-4 | TBD | libib_reconcile CLI + tests + CI |
| Rec-5 | TBD | Integration run against real data; polish |

---

## Key Decisions & Lessons Learned

- **This is a public community tool** — design for other users, not just Jennifer's
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
- **`filesystem:write_file` over `filesystem:edit_file`** — full rewrites are more
  reliable when file state may have drifted between reads.
- **Black is strict** — run Black on any new file before considering it done. Even
  one extra blank line will fail CI.
- **Libib API has no items endpoint** — REST API covers only accounts/managers/patrons.
  Items require manual CSV export from Settings.
- **Tuple data model** — all scrapers use plain tuples, not dataclasses. Keep this
  consistent in `nook_to_libib`, `google_to_libib`, and `libib_reconcile`.
- **Import from `lib`, not `lib.openlibrary`** — `lib/__init__.py` re-exports
  everything cleanly. All new code must follow this pattern.
- **Selenium sandbox limitation** — the sandbox does not have Selenium installed.
  Tests for browser logic must mock `By`, `WebDriverWait`, and `EC` via
  `patch.dict("module.__dict__", ...)` rather than patching as attributes.
- **Nook DOM selectors are unknown** — do not write `_parse_items()` without a
  DevTools screenshot. Ask Jennifer to share one before starting Nook-1.
