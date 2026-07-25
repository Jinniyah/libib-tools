# LibibTools — Project Backlog

## Overview

LibibTools is a **public, community-facing tool** on GitHub (`Jinniyah/libib-tools`).
It scrapes personal digital book libraries and exports Libib-compatible CSVs.

This backlog covers three workstreams:

1. **Metadata enrichment** — `lib/enricher.py`: fills missing fields and resolves
   series data for all scrapers
2. **New scrapers** — `nook_to_libib` (Selenium + manual login) and
   `google_to_libib` (Google Books API + OAuth 2.0)
3. **`libib_reconcile`** — compares a Libib export against scrapes from all five
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

### Metadata Enrichment

| Session | Module | Goal |
|---------|--------|------|
| **Enrich-1** | `lib/enricher.py` | Core enricher: Open Library + Google Books metadata; Wikidata series lookup |
| **Enrich-2** | All scrapers | Wire enricher into all five scraper pipelines; tests; CI |

### New Scrapers

| Session | Module | Goal |
|---------|--------|------|
| **Nook-1** | `nook_to_libib` | ✅ Complete — scaffold, manual login, ISBN-from-DOM scraping, enrichment wired in from the start |
| **Nook-2** | `nook_to_libib` | ✅ Complete — tests, CI, README section. Reconciler provider-tag update deferred until `libib_reconcile` is scaffolded (Rec-1) |
| **Google-1** | `google_to_libib` | OAuth 2.0 setup guide; API client; paginate Purchased shelf (ID 7); CSV output |
| **Google-2** | `google_to_libib` | Tests, CI, README section, add `google` to reconciler provider list |

### Reconciler

| Session | Epics | Goal |
|---------|-------|------|
| **Rec-1** | Epic 1 + scaffolding | Parse Libib export; tag normalization; provider classification; filters. By end: can load and classify the real export. |
| **Rec-2** | Epic 2 | ✅ Complete — matching engine: ISBN-exact first (provider-agnostic), fuzzy fallback (provider-scoped, greedy) with confidence scoring. |
| **Rec-3** | Epics 3 + 4 | ✅ Complete — ISBN enrichment for gap books + all output files (gap CSV + 4 reports). |
| **Rec-4a** | Refactor | ✅ Complete — extracted `run()` from `main()` in all four scrapers' `core.py`; added `wait_fn` param to Chirp/Kobo/Nook `_login()`. Enables `--scrape` to call scrapers in-process instead of shelling out, and lets the future GUI reuse the same entry points. |
| **Rec-4** | Epics 5 + 6 | ✅ Complete — CLI (using the new `run()` entry points), full test suite (232 total, 87% coverage), CI integration. `python -m libib_reconcile` works — verified end-to-end. |
| **Rec-5** | Integration | Run against real data; tune thresholds; polish. |

### Web GUI

A local web app (`webapp/`) wrapping all four scrapers plus the reconciler —
portfolio-quality, security/maintainability-conscious. **Depends on Rec-4a**
(the `run()`/`wait_fn` refactor) and benefits from the reconciler being complete,
so this work starts after the Reconciler sessions above. Full architecture and
ticket breakdown in the `## Web GUI (webapp)` section below.

| Session | Goal |
|---------|------|
| **GUI-Backend-1** | ✅ Complete — `webapp/` skeleton, app factory, `127.0.0.1` binding, dashboard route w/ tool cards + disabled Google placeholder |
| **GUI-Backend-2** | ✅ Complete — job registry + runner (thread-based, in-memory) |
| **GUI-Backend-3** | ✅ Complete — log bridge + Server-Sent Events streaming |
| **GUI-Backend-4** | ✅ Complete — manual-login wait/continue/cancel wiring; cooperative cancel (between pages/books/retries — `GUI-BACKEND-4a`) and a Quit/shutdown button also complete. Tier-2 force-stop (`driver.quit()` mid-Selenium-call) remains deferred |
| **GUI-Backend-5** | ✅ Complete — scraper dispatch endpoints + safe downloads |
| **GUI-Frontend-1** | ✅ Complete — shared `static/style.css` design system + base layout |
| **GUI-Frontend-2** | ✅ Complete — dashboard page, real tool explanations, live status badges, Reconcile card |
| **GUI-Frontend-3** | ✅ Complete — run-a-scraper page, form, live log, manual-login UI. Browser-verified end-to-end 2026-07-23 (all four scrapers, real logins) — see "GUI log visibility" and "Enrichment reliability" entries below for what that testing found. |
| **GUI-Reconcile-1..3** | ✅ Complete (2026-07-24) — reconcile job page + interactive checkbook-style review (search/rank candidates, confirm matches, finalize into a reviewed gap CSV + tag-suggestions report). Superseded the original upload-based plan — see the full writeup below. |
| **GUI-Settings-1** | Read-only credential/env-var status page |
| **GUI-Security-1** | CSRF (Origin-check), path-traversal sweep, shutdown cleanup review |
| **GUI-Polish-1** | README, `docs/CLAUDE.md`, coverage config |

**Next up:** `Rec-5` — integration run against real data. Not started yet;
the user is about to run a full scrape across all four providers (now
validated end-to-end) specifically to produce the real scrape CSVs this
needs, plus a fresh Libib export (the committed reference file is from
2026-06-09 and is stale). See `### Rec-5 — Integration & Polish` below and
the `docs/CLAUDE.md` Session History "Rec-5" row for the exact next command.

---

## Status Key

| Symbol | Meaning |
|--------|---------|
| `[ ]` | Not started |
| `[~]` | In progress |
| `[x]` | Complete |

---

## Metadata Enrichment (`lib/enricher.py`)

### Overview

A new shared enrichment stage runs after ISBN resolution in every scraper pipeline,
before `write_csv()`. It fills fields that scrapers leave blank and resolves series
information from external sources.

**Fields populated by enrichment:**

| Libib column | Source(s) | Notes |
|--------------|-----------|-------|
| `ean_isbn13` | Open Library, Google Books | Only filled if still missing after scraper |
| `upc_isbn10` | Open Library, Google Books | Only filled if still missing after scraper |
| `description` | Open Library, Google Books | Prefer Open Library; fall back to Google Books |
| `publisher` | Open Library, Google Books | Same preference order |
| `publish_date` | Open Library, Google Books | Same preference order |
| `length_of` | Open Library (`number_of_pages`) | Page count |
| `price` | Kept if already present in data; not fetched | No reliable free source |
| `group` | Wikidata series lookup | Series name; blank if not a series |
| `notes` | Series position prepended | See format below |

**Series notes format:**
- Series found, position known: `Series: The Dragon Knight #009 || Additional Notes: <original>`
- Series found, position unknown: `Series: The Dragon Knight #ZZZ || Additional Notes: <original>`
- Not a series: `notes` field unchanged

**Lookup sources and fallback chain:**

For metadata (description, publisher, publish_date, length_of, missing ISBNs):
```
Open Library (by ISBN if available, else title+author search)
  → Google Books Metadata API (no auth, free)
  → AI provider fallback (optional, env-var enabled — see AI Fallback section)
  → leave blank
```

**AI fallback is metadata-only.** It never runs for series/`group` resolution —
that stays Wikidata-only with `#ZZZ` for unknown positions, per the earlier
decision that LLM-based series guesses risk silent hallucination.

For series data:
```
Wikidata SPARQL (by ISBN-13 if available, else title+author)
  → leave group blank; stamp notes with #ZZZ if Wikidata confirms series but lacks position
  → if Wikidata has no series record at all: group and notes unchanged
```

**Important:** The Google Books Metadata API (public, no OAuth) is distinct from the
Google Books Library API (OAuth, used by `google_to_libib`). Enrichment uses only
the public metadata endpoint — no credentials required.

**Price:** If `price` is already populated in the scrape data, keep it. Do not fetch
price from any external source — coverage from free APIs is too low to be useful.

### Enrich-1 — Core enricher (`lib/enricher.py`)

- [x] `ENR-1` Define `EnrichmentResult` dataclass:
  `isbn13, isbn10, description, publisher, publish_date, length_of, series_name, series_position`
  (all `str | None`; `series_position` is the raw integer or `None`)
- [x] `ENR-2` `_fetch_open_library(isbn13, title, author) -> dict` — call Open Library
  Works API; extract description, publisher, publish_date, number_of_pages, isbn_10/isbn_13.
  Prefer ISBN lookup (`/works/{id}.json` + `/editions`); fall back to title+author search.
  Use existing `sleep_between_requests()` from `lib`.
- [x] `ENR-3` `_fetch_google_books_metadata(isbn13, title, author) -> dict` — call
  `https://www.googleapis.com/books/v1/volumes?q=isbn:{isbn}` (or `intitle+inauthor`);
  extract description, publisher, publishedDate, pageCount. No auth required.
  Use `sleep_between_requests()`.
- [x] `ENR-4` `_fetch_wikidata_series(isbn13, title, author) -> tuple[str | None, int | None]` —
  SPARQL query against `https://query.wikidata.org/sparql`. Query by ISBN-13 first
  (`wdt:P212`); fall back to title+author match. Extract series name (`wdt:P179 → label`)
  and series ordinal (`wdt:P1545`). Return `(series_name, position_int)` or `(None, None)`.
- [x] `ENR-5` `enrich_book(title, author, isbn13, isbn10, existing_notes) -> EnrichmentResult` —
  orchestrates ENR-2 → ENR-3 → ENR-4; merges results with source preference (Open Library
  over Google Books for metadata fields); returns a single `EnrichmentResult`.
- [x] `ENR-6` `format_series_notes(series_name, series_position, existing_notes) -> str` —
  pure function; formats the notes prefix. Position zero-padded to 3 digits (`{pos:03d}`).
  If `series_name` is not None and `series_position` is None, uses `#ZZZ`.
  If `series_name` is None, returns `existing_notes` unchanged.
- [x] `ENR-7` Export `enrich_book` and `format_series_notes` from `lib/__init__.py`
- [x] `ENR-8` `tests/test_enricher.py` — unit tests with mocked HTTP:
  - Open Library hit: all fields populated
  - Open Library miss → Google Books hit: correct fallback behaviour
  - Both miss: `EnrichmentResult` all-None (no crash)
  - Wikidata series hit with position: notes formatted correctly
  - Wikidata series hit without position: `#ZZZ` stamp
  - Wikidata miss: notes unchanged
  - `format_series_notes` edge cases: position 1, 99, 100, None, existing notes present,
    existing notes empty
- [x] `ENR-9` Black-format and verify CI passes

### Enrich-2 — Wire enricher into all scrapers

- [~] `ENR-10` Update scraper pipeline signature in all five scrapers to call
  `enrich_book()` after `resolve_isbns()`, before `write_csv()`. Pipeline becomes:
  ```
  resolve_isbns() → enrich_books() → write_csv()
  ```
- [x] `ENR-11` `chirp_to_libib/core.py` — add enrichment step; map `EnrichmentResult`
  fields into the 28-column row dict; populate `group` and prepend series notes
- [x] `ENR-12` `kindle_to_libib/core.py` — same as ENR-11
- [x] `ENR-13` `kobo_to_libib/core.py` — same as ENR-11
- [ ] `ENR-14` `nook_to_libib/core.py` — include enrichment step in initial scaffold
  (Nook-1 should build with enrichment wired in from the start)
- [ ] `ENR-15` `google_to_libib/core.py` — include enrichment step in initial scaffold;
  note that Google Books API already supplies description/publisher/publish_date/ISBNs,
  so enrichment for Google Books books skips Open Library + Google Books metadata calls
  and only runs the Wikidata series lookup
- [~] `ENR-16` `--no-enrich` CLI flag on all five scrapers — skips enrichment step entirely
  (useful for fast runs or offline use)
- [x] `ENR-17` Update `tests/test_chirp.py`, `test_kindle.py`, `test_kobo.py` — mock
  `enrich_book` in enrichment-path tests; ensure existing tests still pass with enrichment
  wired in
- [x] `ENR-18` README — add Enrichment section: data sources, fields populated, series
  format, `--no-enrich` flag, note that price is not fetched
- [x] `ENR-19` Full test suite passes; CI green

### AI-1 — AI provider fallback for metadata

**Scope:** Metadata fields only (`description`, `publisher`, `publish_date`, `length_of`,
missing ISBNs). **Never used for series/`group` resolution** — series stays Wikidata-only.
Opt-in: if no provider is configured via env var, this stage is skipped entirely and the
fallback chain behaves exactly as it does today (Open Library → Google Books → blank).

**Provider selection:** env var only, no CLI flag, no config file.
- `AI_PROVIDER` — e.g. `openai` (unset = AI fallback disabled)
- `OPENAI_API_KEY` — provider-specific key, same pattern as other credential env vars

Designed as a provider abstraction so additional providers (Anthropic, etc.) can be
added later without changing the enricher's call site.

- [x] `AI-1` Define provider-agnostic interface: `_fetch_ai_metadata(provider, title, author, isbn) -> dict`
  in `lib/enricher.py` (or a new `lib/ai_lookup.py` if it grows large)
- [x] `AI-2` `_fetch_openai_metadata(title, author, isbn) -> dict` — call OpenAI's API,
  prompt for structured JSON output (description, publisher, publish_date, page count only);
  parse and validate the response; never fabricate an ISBN via AI (ISBN resolution is
  Open Library's job, not the AI fallback's)
- [x] `AI-3` Wire into `enrich_book()`'s metadata fallback chain, after Google Books
  Metadata API and before "leave blank"; confirm series/`group` path is completely
  untouched by this change
- [x] `AI-4` Read `AI_PROVIDER` env var to select provider at runtime; if unset, skip the
  AI stage entirely (no API calls, no errors, silent no-op)
- [x] `AI-5` Read provider-specific API key from env var (`OPENAI_API_KEY` for `openai`);
  clear from any debug output/logs, consistent with existing credential-clearing pattern
- [x] `AI-6` Graceful failure handling — any API error, timeout, rate limit, or malformed
  JSON response falls through to "leave blank" and logs a warning; never crashes the pipeline
- [x] `AI-7` `tests/test_ai_lookup.py` (or add to `test_enricher.py`) — mocked OpenAI
  responses: hit, miss, malformed JSON, API error, `AI_PROVIDER` unset (confirm no-op)
- [x] `AI-8` README — new section: how to enable AI fallback, required env vars, explicit
  note that it's metadata-only and disabled by default
- [x] `AI-9` Update `docs/CLAUDE.md` fallback chain diagram and Key Decisions with this design
- [x] `AI-10` Black-format, full test suite passes, CI green

---

## Nook Scraper (`nook_to_libib`) — COMPLETE (Nook-1 + Nook-2, 2026-07-21)

### Confirmed DOM selectors (from DevTools, 2026-07-21, nook.barnesandnoble.com/my_library/ebook)

- Container: `ul > li[data-test="{ISBN-13}"]` — one `<li>` per book, ISBN-13 on `data-test`
- Tile: `div.equator-tile.book.new-product-tile > div.south > div.info-section`
- Title link: `div.title > a` — has `data-product-id` (ISBN, redundant), `data-product-title`
  (full untruncated title — use this, not the `<li>` display text, which is
  ellipsis-truncated via `text-overflow:ellipsis;width:160px`), `href="/products/{isbn}/sample"`
- Author link: sibling `<a href="http://www.barnesandnoble.com/search?q={Author}">` right
  after the title div, inside the same `info-section` — link text is the author name
- Cover image: `<img data-bntrack="LinkedImage" src="{cover-url}" alt="Cover Image: {title}" title="{title}">`
  — read `src` directly, no `srcset` parsing needed (confirmed 2026-07-21, closing out the
  gap the DOM capture above didn't originally cover)

This unblocked Nook-1: ISBN, title, author, and cover are all available without fighting
the truncated display text.

**Pagination:** unconfirmed at scale — Jennifer's own library is small enough that no
next-page control appears, so `scrape_nook()` scrapes a single page load. `--pages` is
still accepted on the CLI for consistency but is currently a no-op. Revisit if a larger
library is tested and pagination/infinite-scroll turns out to be needed.

**Approach:** Selenium + manual login pause (same pattern as Chirp — not Kobo's two-tab
trick, which exists specifically to defeat hCaptcha; B&N uses Akamai instead).
Library URL used: `https://nook.barnesandnoble.com/my_library/ebook` (this is where the
confirmed selectors above were captured from — note this differs from the
`barnesandnoble.com/account/my-digital-library` URL mentioned in earlier planning notes).

**ISBN comes from the DOM, not a lookup.** Because `data-test` on the container is the
ISBN-13, `resolve_isbns()` in `nook_to_libib` only calls Open Library as a fallback for
the rare book missing one — unlike Chirp/Kindle/Kobo, where every book needs a live
lookup. See `docs/CLAUDE.md` for the full pipeline diagram.

### Nook-1 — Core scraper

- [x] `NOOK-1` Scaffold `nook_to_libib/` with `__init__.py`, `__main__.py`, `core.py`
- [x] `NOOK-2` Add `nook_to_libib` to `packages` in `pyproject.toml`
- [x] `NOOK-3` `_build_driver()` — same anti-fingerprint flags as Chirp/Kobo
- [x] `NOOK-4` `_login()` — manual pause flow; verify library grid visible before continuing
- [x] `NOOK-5` `_parse_items()` — extract `(title, author, isbn, cover_url)` from DOM;
  ISBN read directly from `data-test` on the container (no separate lookup needed)
- [x] `NOOK-6` `scrape_nook()` — single page load (pagination unconfirmed at scale, see above);
  `--pages` accepted for CLI consistency but currently a no-op
- [x] `NOOK-7` `resolve_isbns()` — trusts the scraped ISBN; only calls `get_isbn` +
  `sleep_between_requests` from `lib` as a fallback when `data-test` is missing/empty
- [x] `NOOK-8` `enrich_books()` — call `enrich_book` from `lib` per resolved book (see ENR-14);
  wired in from the initial scaffold, not retrofitted
- [x] `NOOK-9` `write_csv()` — 28-column Libib CSV, tag = `nook,ebook`, UTF-8-sig
- [x] `NOOK-10` `write_unresolved()` — txt report of books without ISBNs
- [x] `NOOK-11` `main()` + CLI: `--pages`, `--dry-run`, `--output-dir`, `--no-enrich`
- [x] `NOOK-12` Black-format and verify CI passes

### Nook-2 — Tests, CI, docs

- [x] `NOOK-13` `tests/test_nook.py` — 21 tests covering `_parse_items`, `_dedupe_and_filter`
  (including the ISBN-keyed cover reattachment), `resolve_isbns` (trust + fallback paths),
  `enrich_books` (mocked), `write_csv`, `write_unresolved`, CLI flags
- [x] `NOOK-14` Add `nook_to_libib` to `[tool.coverage.run] source` in `pyproject.toml`
- [x] `NOOK-15` README — Nook section: manual login instructions, output files, CLI flags,
  `--pages` no-op caveat, ISBN-from-DOM note
- [x] `NOOK-16` Update `docs/CLAUDE.md` — add Nook to providers table, login strategies,
  and its own pipeline diagram (it diverges from Chirp/Kindle/Kobo)
- [ ] `NOOK-17` Update `libib_reconcile` provider classifier to recognise `nook` tag —
  **deferred**: `libib_reconcile` hasn't been scaffolded yet (Rec-1 is still `[ ]`).
  The classifier's planned keyword list (`LIB-3`) already includes `nook`; nothing to
  change until that module exists.
- [x] `NOOK-18` Full test suite passes; CI green (123 tests, ruff/black/mypy clean)

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
- Returns: title, authors, ISBNs (industryIdentifiers), thumbnail, description,
  publisher, publishedDate, pageCount — **enrichment skips metadata calls for Google
  books that already have these fields; only Wikidata series lookup runs**
- Pagination: `startIndex` + `maxResults` (max 40 per page)

### Google-1 — Core scraper

- [ ] `GOOG-1` Scaffold `google_to_libib/` with `__init__.py`, `__main__.py`, `core.py`
- [ ] `GOOG-2` Add `google_to_libib` to `packages` in `pyproject.toml`
- [ ] `GOOG-3` Add `google-api-python-client` and `google-auth-oauthlib` to `requirements.txt`
- [ ] `GOOG-4` OAuth 2.0 flow: load credentials from `~/.config/libibtools/google_token.json`;
  run browser consent on first use; auto-refresh thereafter
- [ ] `GOOG-5` `fetch_all_books()` — paginate through Purchased shelf; extract
  `(title, author, isbn, cover_url, description, publisher, publish_date, page_count)`
  directly from API response
- [ ] `GOOG-6` ISBN extraction from `industryIdentifiers` array — prefer ISBN-13,
  fall back to ISBN-10, then Open Library lookup if neither present
- [ ] `GOOG-7` `enrich_books()` — call `enrich_book` from `lib` per book; pass
  already-populated metadata fields through so enricher skips redundant lookups
  (only Wikidata series query runs for books with full metadata)
- [ ] `GOOG-8` `write_csv()` — 28-column Libib CSV, tag = `google,ebook`, UTF-8-sig
- [ ] `GOOG-9` `write_unresolved()` — txt report of books without ISBNs
- [ ] `GOOG-10` `main()` + CLI: `--dry-run`, `--output-dir`,
  `--credentials PATH` (default: `~/.config/libibtools/google_credentials.json`),
  `--no-enrich`
- [ ] `GOOG-11` Black-format and verify CI passes

### Google-2 — Tests, CI, docs

- [ ] `GOOG-12` `tests/test_google.py` — unit tests with mocked API responses:
  pagination, ISBN extraction, enrichment (mocked), CSV output, unresolved output
- [ ] `GOOG-13` Add `google_to_libib` to `[tool.coverage.run] source` in `pyproject.toml`
- [ ] `GOOG-14` README — Google Books section: OAuth setup walkthrough (Google Cloud
  Console steps), credentials file location, CLI flags
- [ ] `GOOG-15` README — note that `--pages` is not needed (API returns full library
  in one paginated batch)
- [ ] `GOOG-16` Update `docs/CLAUDE.md` — add Google to providers table
- [ ] `GOOG-17` Update `libib_reconcile` provider classifier to recognise `google` tag
- [ ] `GOOG-18` Full test suite passes; CI green

---

## Reconciler (`libib_reconcile`)

### Epic 1 — Libib CSV Parser — COMPLETE (2026-07-21)
**File:** `libib_reconcile/libib_reader.py`
**Session:** Rec-1

- [x] `LIB-1` Parse Libib export CSV; handle UTF-8-sig encoding and quoted fields with embedded commas
- [x] `LIB-2` Tag normalizer: lowercase → split on `,` → strip whitespace → return `set[str]`
- [x] `LIB-3` Provider classifier: scan normalized tag set; return `set[str]` of detected providers (`kindle`, `kobo`, `chirp`, `nook`, `google`, `digital_unknown`)
- [x] `LIB-4` Entry filter: skip any entry whose tag set contains `deleted` or `removed`
- [x] `LIB-5` Entry filter: skip entries with no digital provider keywords at all (physical-only books)
- [x] `LIB-6` Flag `digital`-only entries (tag set contains `digital` but no named provider) as ambiguous
- [x] `LIB-7` Extract existing ISBNs from `ean_isbn13` and `upc_isbn10` fields for use as primary match keys

### Scaffolding tasks (also Rec-1) — COMPLETE
- [x] `SCAFFOLD-1` Create `libib_reconcile/` with `__init__.py`, `__main__.py`, `core.py`, `libib_reader.py`, `reconciler.py`, `isbn_enricher.py`, `output.py`
- [x] `SCAFFOLD-2` Add `libib_reconcile` to `packages` in `pyproject.toml`
- [x] `SCAFFOLD-3` Create `tests/test_libib_reader.py` — tag normalizer, provider classifier, all filters
- [x] `SCAFFOLD-4` Verify full test suite still passes after scaffolding

### Epic 2 — Reconciliation Engine — COMPLETE (2026-07-22)
**File:** `libib_reconcile/reconciler.py`
**Session:** Rec-2

Two-pass, two-pool consumption model: ISBN-exact is provider-agnostic and runs
first (also the only way an `ambiguous` "digital"-only entry can resolve);
fuzzy title/author is provider-scoped to the entry's own tags and runs second,
greedy-assigned by descending title-similarity score (not an optimal bipartite
solver — not worth the complexity at personal-library scale).

- [x] `REC-1` ISBN-exact match: if both sides have an ISBN, match on that first; highest confidence
- [x] `REC-2` Title+author fuzzy fallback using `_title_is_plausible()` from `lib/openlibrary.py`
- [x] `REC-3` Confidence scoring: `exact_isbn` = high, `fuzzy_title_author` = medium, `title_only` = low
- [x] `REC-4` Provider-aware matching: `kindle, kobo` entry checks both scrapes; match on either = matched
- [x] `REC-5` Cross-format: `chirp, kindle` entry matches either Chirp or Kindle scrape
- [x] `REC-6` Classify each scraped book: `matched` or `missing_from_libib`
- [x] `REC-7` Classify each Libib entry: `matched`, `libib_only` (orphan), `ambiguous`, or `out_of_scope`
- [x] `REC-8` Dedup scraped books before comparing (reuse `dedupe_books_by_title` from `lib`) —
  found and fixed a real bug during this session: reattaching cover URLs via an
  ISBN-keyed dict silently collided/dropped covers whenever multiple scraped
  books shared an empty/unresolved ISBN. Fixed by packing isbn+cover into the
  dedupe helper's opaque 3rd field instead of a lossy dict lookup.
- [x] `REC-9` Unit tests in `tests/test_reconcile.py` — 22 tests

### Epic 3 — ISBN Enrichment
**File:** `libib_reconcile/isbn_enricher.py`
**Session:** Rec-3 — COMPLETE (2026-07-22)

- [x] `ISBN-1` For each `missing_from_libib` book, call `get_isbn(title, author)` from `lib`
- [x] `ISBN-2` Skip Open Library lookup if book already has an ISBN (Google API provides these directly)
- [x] `ISBN-3` Respect rate limiting via `sleep_between_requests()` from `lib`
- [x] `ISBN-4` Log progress at `ISBN_LOG_INTERVAL = 25`
- [x] `ISBN-5` Track and report enrichment rate — via log lines (`"ISBN enrichment
  complete: %d/%d gap book(s) resolved."`), matching the pattern every scraper's
  own `resolve_isbns()` already uses, rather than a separately-returned stats object
- [x] `ISBN-6` Unit tests: mock `get_isbn` and verify enrichment — 6 tests in
  `tests/test_isbn_enricher.py`

### Epic 4 — Output Files — COMPLETE (2026-07-22)
**File:** `libib_reconcile/output.py`
**Session:** Rec-3

- [x] `OUT-1` **Gap CSV**: missing books ready to import into Libib; full 28-column format;
  correct `tags` per provider; ISBN-populated where available; enrichment applied
  (description, publisher, publish_date, length_of, group, notes) via `enrich_book`
- [x] `OUT-2` **Reconciliation report** (`.txt`): summary counts + per-provider breakdown
- [x] `OUT-3` **Orphan report** (`.txt`): Libib entries not found in any scrape
- [x] `OUT-4` **Low-confidence match report** (`.txt`): fuzzy matches needing human review —
  covers both `medium` and `low` confidence, since any fuzzy match is worth a human glance
- [x] `OUT-5` **Ambiguous report** (`.txt`): `digital`-only entries with no named provider
- [x] `OUT-6` Timestamp all output files: `reconcile_YYYY-MM-DD_HH-MM_<type>.{csv,txt}` —
  every writer takes an already-computed `timestamp` string rather than generating its
  own, so one reconcile run's files share a single timestamp and each writer stays
  independently testable without freezing the clock
- [x] `OUT-7` Unit tests: CSV columns, tag assignment — 16 tests in
  `tests/test_reconcile_output.py`. "Dry-run writes nothing" is a CLI-level behavior
  (core.py simply not calling these writers), verified as part of Rec-4, not here.

### Epic 4a — Core Refactor: extract `run()` from `main()` — COMPLETE (2026-07-22)
**Files:** `chirp_to_libib/core.py`, `kindle_to_libib/core.py`, `kobo_to_libib/core.py`, `nook_to_libib/core.py`
**Session:** Rec-4a

Enables `libib_reconcile --scrape` to call scrapers in-process instead of
shelling out to `python -m chirp_to_libib` etc., and lets a future GUI reuse
the same entry points without inventing this abstraction twice.

- [x] `REFACTOR-1` Extract the body of `main()` in each scraper into
  `run(*, pages, dry_run, output_dir, no_enrich, wait_fn=_default_wait) -> RunResult` —
  explicit keyword args instead of an `argparse.Namespace`, returns a small
  result object (paths written, counts) instead of only printing
- [x] `REFACTOR-2` `main()` becomes a thin wrapper: `parse_args()` → `run(...)` with
  parsed values → print final message. Existing CLI behavior unchanged (verified:
  same log lines, same final "Upload '...' to Libib" message).
- [x] `REFACTOR-3` Add `wait_fn: Callable[[], None] = _default_wait` param to
  `_login()` in Chirp/Kobo/Nook. `_default_wait` bundles the exact print+input
  each scraper already had (its own instructional text preserved verbatim) —
  `_login()` itself no longer prints anything, it just calls `wait_fn()`.
  Default preserves current CLI behavior exactly — purely additive change.
- [x] `REFACTOR-4` Kindle gets the same `run()` extraction for CLI/GUI symmetry
  (no `wait_fn` — automated login, unchanged). `run()` deliberately takes no
  email/password params — credentials stay env-var/prompt-only via the existing
  `_prompt_credentials()`, consistent with the GUI's "never accept credentials
  through a browser form" design decision.
- [x] `REFACTOR-5` Verify every existing test still passes — of the 203 tests
  that predated this refactor, only one needed a change. The exception:
  `test_pipeline.py`'s
  `mock_scrape.assert_called_once_with(...)` needed updating to expect the new
  `wait_fn=` kwarg now threaded through `scrape_chirp()` — a correct, intentional
  consequence of the new parameter, not a behavior regression, so this still
  counts as "no behavior change," just one assertion learning about a real new arg.
- [x] `REFACTOR-6` New unit tests for `run()` directly (`scrape_x` mocked) — added
  across `test_pipeline.py` (Chirp), `test_kindle.py`, `test_kobo.py`, `test_nook.py`:
  paths/counts on success, empty result when nothing scraped, dry-run returns no
  paths, `wait_fn` threads through to `scrape_*()`. Left `core.py:main` in the
  coverage omit list — it's now a few lines of trivial glue, appropriately excluded;
  `run()` itself is *not* omitted and is the thing the new tests actually cover.
- [x] `REFACTOR-7` Black-format, ruff, mypy, full suite green — 216 tests, coverage
  79% (gate is 65%)

### Epic 5 — CLI & Orchestration — COMPLETE (2026-07-22)
**Files:** `libib_reconcile/__main__.py`, `libib_reconcile/core.py`
**Session:** Rec-4

- [x] `CLI-1` `--libib PATH` — Libib export CSV (required)
- [x] `CLI-2` `--scrape` — trigger live scrapes by calling each scraper's `run()`
  entry point in-process (via `importlib`, lazily — see design note below), not
  by shelling out. Always called with `no_enrich=True`: enriching every scraped
  book up front is wasteful when only the gap subset ultimately needs it —
  `enrich_gap_books()` (Rec-3) enriches once reconciliation has narrowed the set.
- [x] `CLI-3` `--kindle PATH` / `--kobo PATH` / `--chirp PATH` / `--nook PATH` /
  `--google PATH` — accept pre-existing scrape CSVs. Both this and `--scrape`
  output converge on `_load_scrape_csv()`, which reads any `LIBIB_HEADERS`-shaped
  CSV back into `(title, author, isbn, cover)` tuples — one interchange format
  either way. `--google` is accepted even though `google_to_libib` isn't built
  yet (someone could supply a manually-prepared CSV); `--scrape` with `google`
  in `--providers` just logs a warning and skips it.
- [x] `CLI-4` `--output-dir PATH` — consistent with other tools
- [x] `CLI-5` `--providers kindle kobo chirp nook google` — limit to specific providers
- [x] `CLI-6` `--dry-run` — suppresses only `run()`'s own output files (summary,
  gap CSV, orphan/low-confidence/ambiguous). If `--scrape` is used, each
  provider scrape still runs for real and writes its own intermediate CSV —
  that's the data source reconciliation needs, not a side effect `--dry-run`
  can skip under the current scraper `run()` contract. Documented explicitly
  in `run()`'s docstring so this isn't a surprise later.
- [x] `CLI-7` `--no-enrich` — skip enrichment on gap CSV (fast mode)
- [x] `CLI-8` Validate at least one provider source supplied; exit with clear
  error if not (`ValueError` in `run()`, caught and turned into `SystemExit` in `main()`)
- [x] `CLI-9` `__main__.py`: `from .core import main` + `if __name__ == "__main__": main()`

Follows the same `run()`/`main()` split as the four scrapers (Rec-4a):
`run(*, libib_path, output_dir, providers, scrape, chirp, kindle, kobo, nook,
google, dry_run, no_enrich) -> ReconcileRunResult`, callable directly without
argparse. Verified end-to-end with a real (non-mocked) CLI invocation —
`python -m libib_reconcile --libib ... --kindle ... --no-enrich` — producing
correct gap CSV + summary counts against the Rec-1 fixture.

### Epic 6 — Tests & CI — COMPLETE (2026-07-22)
**Session:** Rec-4

- [x] `TST-1` Tag normalization edge cases — `tests/test_libib_reader.py` (Rec-1)
- [x] `TST-2` Provider classification — `tests/test_libib_reader.py` (Rec-1)
- [x] `TST-3` ISBN-exact matching — `tests/test_reconcile.py` (Rec-2)
- [x] `TST-4` Fuzzy matching — `tests/test_reconcile.py` (Rec-2)
- [x] `TST-5` Provider-aware matching — `tests/test_reconcile.py` (Rec-2)
- [x] `TST-6` Cross-format matching — `tests/test_reconcile.py` (Rec-2)
- [x] `TST-7` Gap CSV columns/tags/enrichment — `tests/test_reconcile_output.py` (Rec-3)
- [x] `TST-8` Dry-run: no files on disk — `tests/test_reconcile_core.py`,
  `test_run_dry_run_no_files_on_disk` (asserts `os.listdir(tmp) == []`)
- [x] `TST-9` Integration test: fixture CSVs with known gap → assert correct counts —
  `tests/test_reconcile_core.py::test_run_integration_known_gap`, using
  `tests/fixtures/libib_export_sample.csv` + new `tests/fixtures/scrape_kindle_sample.csv`
  (one book that matches an existing Libib entry by ISBN, one genuinely new book) —
  runs the real, non-mocked pipeline end-to-end and asserts the gap CSV contains
  exactly the new book and the summary reports the correct counts
- [x] `TST-10` Added `libib_reconcile` to `[tool.coverage.run] source` in
  `pyproject.toml`, plus `omit` entries for `__main__.py` and `core.py:main`
  (trivial glue, same as every scraper)
- [x] `TST-11` Full suite passes; CI green — 232 tests, coverage 87% for the
  three `source`-tracked packages combined (gate is 65%)

### Rec-5 — Integration & Polish
*Partially validated 2026-07-22 (parser only — see below); the rest needs live
scrapes, which need a human present for manual login. Not broken into full
tickets yet — depends on those real scrape results.*

**Update 2026-07-23:** the manual-login step this was blocked on is now fully
validated end-to-end through the web GUI (all four scrapers, see
`docs/CLAUDE.md`'s "GUI-live-testing" session row) — the user is about to run
real full scrapes via `python -m webapp` for exactly this purpose. Once those
CSVs exist (plus a fresh Libib export — the one below is from 2026-06-09),
this is unblocked: either point `libib_reconcile`'s CLI at the scraped CSVs
directly (`--chirp <path> --kindle <path> ...`), or use its `--scrape` flag to
drive the scrapers itself, `no_enrich=True` per book (see "`--scrape` /
pre-existing-CSV design" in `docs/CLAUDE.md`).

**Parser validated against the real export** (`libib_library_export_20260609_225934.csv`):
`read_libib_export()` parses all 3461 real rows in ~0.13s, zero errors, zero
active entries left with an unclassified empty provider set. (Note: the file
is 7026 *lines* but 3461 CSV *rows* — multi-paragraph description fields
contain embedded newlines inside quoted fields, which `wc -l` counts but the
`csv` module correctly doesn't; don't be alarmed by the line-count mismatch.)
Real provider breakdown: kindle 1935, chirp 514, nook 147, kobo 54, google 11,
digital_unknown (ambiguous) 6. A few tag tokens (`audible`, `bn`, `humblebundle`,
`mp3`) aren't recognized as providers, but that's correct, not a bug — Audible
has no scraper in this project (not Chirp), so those entries correctly fall to
`digital_unknown`; `bn` is redundant with an accompanying `nook` tag.

- [ ] Run against real scrapes from all providers (needs live browser + manual
  login — human required)
- [ ] Review reconciliation report and orphan report for accuracy against real matches
- [ ] Tune `_title_is_plausible()` threshold using real fuzzy-match results
- [ ] Review low-confidence matches
- [ ] Import gap CSV into Libib and verify it loads cleanly
- [ ] Update `README.md` to document `libib_reconcile` (CLI usage, flags, output files)

---

## Web GUI (`webapp`)

### Architecture summary

- New top-level package `webapp/` — FastAPI + Jinja2 + one shared `static/style.css`
  design system + minimal hand-written vanilla JS. No SPA framework, no htmx, no
  frontend build step — this is a local, single-user tool, not a product, and a
  build pipeline would be complexity without payoff.
- Selenium scrapes are synchronous end-to-end (no `await`-able API), so each scrape
  job runs in a real OS thread (`threading.Thread`), never as an `asyncio` task —
  running blocking Selenium code inside `async def` would freeze the whole server,
  including the log stream telling the browser what's happening.
- An in-memory job registry (`dict[str, Job]` behind a `threading.Lock`) tracks
  status. No SQLite/Celery/Redis — this is deliberately right-sized for a
  single-user, single-process, local tool; losing in-flight job history on a
  server restart is an acceptable, explicit tradeoff, not an oversight.
- Log streaming via Server-Sent Events (`StreamingResponse`, `text/event-stream`)
  off a per-job `queue.Queue`. One global `logging.Handler`, attached once at
  startup, maps `threading.get_ident()` → job and pushes matching log records
  onto that job's queue — no changes needed to any scraper's existing
  `log.info(...)` call sites. SSE over WebSocket because the browser only needs
  one-directional push; Continue/Cancel are independent, infrequent, and map
  cleanly onto ordinary `POST` requests — no bidirectional need, no new dependency
  (native `EventSource`, no client library).
- Manual login (Chirp/Kobo/Nook): `_login()` gets a `wait_fn` parameter (see
  `REFACTOR-3` above). The web layer's `wait_fn` flips the job to
  `waiting_for_login` and polls a `threading.Event` that the browser's "Continue"
  button sets via `POST .../continue` — replacing today's terminal `input()`.
  Default `wait_fn` is unchanged `print(...); input()`, so the CLI is untouched.
- Cancellation: Tier 1 (must-have) — cancel while blocked on the login wait, via
  the same polling loop checking a `cancel_event`, unwinding cleanly through each
  scraper's existing `try/finally: driver.quit()`. Tier 1.5 (cooperative,
  `GUI-BACKEND-4a`, shipped 2026-07-23) — the same `cancel_event` is also checked
  between scrape pages, between each book's ISBN lookup/enrichment call, and
  between individual HTTP retry attempts (`lib/http_retry.py`'s `request_json`),
  raising a shared `lib.cancellation.OperationCancelled`. Tier 2 (nice-to-have,
  still not built) — a "Force Stop" during active scraping that calls
  `driver.quit()` directly from the registry mid-Selenium-call, honestly labeled
  as an interrupt rather than a graceful stop. A FastAPI shutdown hook
  force-quits any still-live drivers so a server restart doesn't orphan Chrome
  processes.
- One active job per provider at a time, enforced by the registry — beyond UI
  clarity, running two concurrent sessions against a CAPTCHA-sensitive site
  (Kobo/Chirp/Nook) from the same browser profile risks getting flagged.

### Security

- Bind `127.0.0.1` only, hardcoded — never `0.0.0.0`. This tool spawns a real
  browser tied to the desktop session and handles credentials in-process; there's
  no reason for it to listen beyond localhost.
- Uploaded Libib CSVs: enforce a max body size before fully buffering, validate
  CSV *shape* (attempt a parse) rather than trusting the extension or
  `Content-Type` alone (both are client-supplied and spoofable), and never use
  the client-supplied filename to construct a filesystem path — generate a
  server-side name (`uuid4().hex + ".csv"`), stage it under a fixed uploads
  directory, and validate any output path with `Path.resolve()` +
  `is_relative_to(base_dir)` before ever writing to or reading from it.
- CSRF: a same-origin policy blocks an attacker page from *reading* a response
  from `127.0.0.1`, but not from *sending* a state-changing request (e.g. an
  auto-submitting hidden form) — a real, documented class of attack against
  localhost dev servers. Mitigate with an Origin/Referer check dependency on
  every `POST`/`DELETE` route, rejecting anything not `http://127.0.0.1:<port>`
  or `http://localhost:<port>`. No session/cookie machinery needed for this.
- Credentials stay env-var/`.env`-only, **never accepted via a browser form** —
  a browser-submitted credential sits in server memory with more accidental leak
  surface (request logging, a future "save settings" feature) for no real
  convenience gain, since the user still types it once either way. The Settings
  page is read-only status (`configured` / `not configured`), never a value.
  Google's future OAuth consent screen is fine to route through the browser
  since the consent page itself is Google's, not ours — the resulting token file
  still never touches rendered HTML.

### GUI-Backend-1 — webapp skeleton — COMPLETE (2026-07-22)
- [x] `WEB-1` App factory (`webapp/main.py`, `webapp/app.py`, `webapp/__main__.py`
  for `python -m webapp`); `127.0.0.1` binding only. Verified with a real
  `uvicorn` process (not just `TestClient`) — dashboard and static CSS both
  return 200.
- [x] `WEB-2` `base.html` Jinja2 template; `static/style.css` stub (design
  tokens only — CSS custom properties incl. a `prefers-color-scheme: dark`
  block; component classes land in GUI-Frontend-1)
- [x] `WEB-3` Dashboard route (`GET /`) — per-tool cards (`ToolCard` dataclass)
  for Chirp/Kindle/Kobo/Nook incl. a disabled Google Books placeholder with
  "Coming soon" + a 1-line explanation of the planned OAuth approach
- [x] `WEB-4` `tests/test_webapp_dashboard.py` — 6 tests: dashboard 200, all
  four live tools render, Google placeholder + "Coming soon" render, enabled
  tools have `/scrape/<slug>` links, Google has none, static CSS served
- [x] `WEB-5` Added `webapp` to `pyproject.toml` `packages` + coverage `source`
  (with `webapp/__main__.py`/`webapp/main.py` omitted, same convention as the
  scrapers). New runtime deps added to `requirements.txt`: `fastapi`,
  `uvicorn[standard]`, `jinja2`, `python-multipart`, `python-dotenv`; `httpx`
  added to `requirements-dev.txt` for `TestClient`.

### GUI-Backend-2 — job registry + runner — COMPLETE (2026-07-22)
- [x] `WEB-6` `Job` dataclass (id, provider, status, created_at, log_queue,
  continue_event, cancel_event, thread, error) — field renamed `result_paths`
  → `result: Any` in the actual implementation: it holds the whole `RunResult`/
  `ReconcileRunResult` object a provider's `run()` returns (which already
  carries the path fields), not a separately-extracted dict. Simpler, and the
  download endpoints (GUI-Backend-5) can pull specific paths off it directly.
- [x] `WEB-7` `JobRegistry` (`webapp/jobs/registry.py`) — `dict[str, Job]`
  behind `threading.Lock`; one-active-job-per-provider guard via
  `JobAlreadyRunningError`
- [x] `WEB-8` `runner.py` — `start_job()` spawns a daemon thread; status
  transitions `queued → running → (waiting_for_login →running)? →
  completed/failed/cancelled`. `run_callable: Callable[[wait_fn], Any]` is how
  the runner stays provider-agnostic — it builds `wait_fn` and hands it to the
  caller-supplied callable, rather than needing to know each scraper's `run()`
  signature (Kindle has no `wait_fn` param at all; the caller just ignores the
  argument for that one).
- [x] `WEB-9` `tests/test_job_runner.py` — 8 tests: full lifecycle with and
  without a login step, exception → `failed`, cancel-during-login-wait →
  `cancelled`, one-active-job-per-provider enforcement, concurrent jobs for
  different providers, registry lookup. All against a fake provider callable —
  zero Selenium, zero real threads.io wait beyond real `threading.Thread`.

### GUI-Backend-3 — log bridge + SSE — COMPLETE (2026-07-22)
- [x] `WEB-10` `log_bridge.py` — `JobLogHandler`, thread-id → job map
  (`register_thread`/`unregister_thread`, called from `runner.py`'s `_run()`),
  `emit()` pushes to `job.log_queue`. Installed once on the root logger via
  `install()`, called at `webapp/app.py` module load — every scraper's
  existing `log.info(...)` calls reach the right job's queue with zero changes
  to any scraper module.
- [x] `WEB-11` `GET /scrape/{provider}/jobs/{job_id}/events` — SSE
  `StreamingResponse`; 404s on unknown job or provider/job mismatch. Drains
  buffered log lines first, only emits `event: done` once the job has reached
  a terminal status *and* the queue is empty, so nothing buffered is ever lost.
- [x] `WEB-12` `tests/test_log_bridge.py` — 5 tests incl. thread isolation
  (job A's logs never leak into job B's queue). Caught a real cross-test
  interaction while writing these: `webapp.app`'s module-level `install()`
  call attaches a `JobLogHandler` to the *root* logger, and `_thread_jobs` is
  shared module state — a test logger with default propagation enabled gets
  double-delivered into the same job's queue (once via its own handler, once
  via the globally-installed one). Fixed in the tests (`log.propagate =
  False`), not in `log_bridge.py` — the global-handler design is correct for
  the real app; test loggers just need the usual isolation discipline.
- [x] `WEB-13` `tests/test_webapp_events.py` — 4 `TestClient` streaming tests:
  buffered-lines-then-done, waits for terminal status before closing (job
  finishes mid-stream from another thread), 404s for unknown job / provider
  mismatch.

### GUI-Backend-4 — manual login wait / continue / cancel — COMPLETE (2026-07-22, Tier 1 only — see below)
- [x] `WEB-14` Web-layer `wait_fn` — done as part of `runner.py`'s
  `_make_wait_fn()` (GUI-Backend-2/3), which flips the job to
  `waiting_for_login` and polls `continue_event` (default CLI behavior
  unchanged — see `REFACTOR-3`)
- [x] `WEB-15` `POST /scrape/{provider}/jobs/{id}/continue` — sets
  `continue_event`; `409` if the job isn't currently `waiting_for_login`,
  `404` for an unknown job or provider mismatch
- [x] `WEB-16` `POST /scrape/{provider}/jobs/{id}/cancel` — sets
  `cancel_event`, observed by the login-wait poll loop *and* (since
  `GUI-BACKEND-4a` below) between scrape pages/books/retries once past login.
  `409` if already terminal.
- [x] `WEB-17` FastAPI shutdown hook (via a `lifespan` context manager, not
  the deprecated `@app.on_event`) — cancels any non-terminal job on shutdown.
- [x] `WEB-18` `tests/test_webapp_job_control.py` — 9 tests: continue
  unblocks a waiting job, continue on a non-waiting job → 409, cancel during
  login-wait → `cancelled`, cancel on an already-finished job → 409, 404s for
  unknown job/provider mismatch on both endpoints, and a real
  `TestClient`-context-exit test proving the shutdown hook cancels a job still
  waiting on login.

### GUI-Backend-4a — cooperative cancel + Quit button — COMPLETE (2026-07-23)
Prompted by manual testing: Chirp enrichment hit a sustained run of Google
Books 429s, and there was no way to stop the run short of killing the process,
nor any way to shut the server down from the GUI at all.
- [x] `lib/cancellation.py` — `OperationCancelled`, shared by the scraper cores
  and `webapp/jobs/runner.py` (replacing the latter's previously-local
  `JobCancelled`).
- [x] `lib/http_retry.py` — centralizes the retry/backoff loop previously
  duplicated (and already drifted, 3 vs 4 `max_retries`) between
  `lib/enricher.py`'s `_http_get_json` and `lib/openlibrary.py`'s `_ol_query`.
  Adds real 429 handling: honors `Retry-After` when present, otherwise waits
  at least 5s (the prior 2/4/8s backoff was provably too short — observed 3
  consecutive 429s from Google Books at that spacing). Accepts an optional
  `cancel_fn`, checked before each attempt and in ~1s slices during any wait.
- [x] All four scraper cores (`chirp_to_libib`, `kindle_to_libib`,
  `kobo_to_libib`, `nook_to_libib`) gained a `cancel_fn: Callable[[], bool] =
  lambda: False` parameter threaded from `run()` down through `scrape_*()`
  (checked once per page), `resolve_isbns()`/`enrich_books()` (checked once
  per book), and down into `lib.get_isbn()`/`lib.enrich_book()`'s retry calls
  — raising `OperationCancelled` cooperatively. Does **not** interrupt a
  Selenium call already in flight — that's still the separately-tracked
  Tier-2 force-stop below.
- [x] `webapp/jobs/runner.py` — `_make_cancel_fn(job)` alongside the existing
  `_make_wait_fn`; `run_callable` signature widened from `(wait_fn)` to
  `(wait_fn, cancel_fn)`. `webapp/app.py`'s `_build_run_callable` detects
  `cancel_fn` in `module.run`'s signature the same way it already detects
  `wait_fn`.
- [x] Frontend: the Cancel button moved out of the login-wait card so it's
  visible for the whole run, not just during login wait (`scrape.html`,
  `app.js`).
- [x] `POST /shutdown` + `GET /api/jobs-live` — cancels any live jobs, then
  `os._exit(0)` after a short delay so the response reaches the browser first
  (no POSIX-signal juggling, cross-platform-safe). A "Quit" button in
  `base.html`'s header confirms first if a job is currently running.
- [x] Fixed a real UX bug found in testing: the Continue button's popup
  stayed visible and re-clickable for up to ~1s after being clicked (the
  button re-enabled on the fetch response, before the backend's 0.2s poll +
  SSE push had a chance to flip status away from `waiting_for_login`), which
  read as "my click didn't register." Fixed by hiding the popup optimistically
  on click (`app.js`).
- [x] Downloads-vs-output-folder note: `Job.output_dir` (resolved to an
  absolute path, skipped for dry runs) is now returned from
  `GET /scrape/{provider}/jobs/{id}` and shown as "Files saved to: ..." next
  to the (still-present) per-file download buttons.

**Still deferred:** Tier-2 force-stop — interrupting a live Selenium call in
progress via `driver.quit()`. Requires each scraper's `scrape_*()` to publish
its live driver (e.g. via a `driver_holder` callback passed into `run()`) so
the registry can call `.quit()` on it directly. Real touch to all four
scraper modules' internals, more invasive than the cooperative checks above —
still deliberately out of scope.

### GUI-Backend-5 — scraper dispatch + downloads — COMPLETE (2026-07-22)
- [x] `WEB-19` `POST /scrape/{provider}/jobs` — `ScrapeOptions` Pydantic body
  (`pages`/`dry_run`/`output_dir`/`no_enrich`); `404` for an unknown/not-yet-built
  provider, `409` via `JobAlreadyRunningError` if that provider already has a
  live job. `_build_run_callable()` uses `inspect.signature()` to detect
  whether the target scraper's `run()` accepts `wait_fn` (Kindle's doesn't) —
  generic across all four scrapers without hardcoding per-provider branches.
- [x] `WEB-20` `GET /downloads/{job_id}/{filename}` — never joins `filename`
  onto a directory. Only ever serves a path that's already one of the job's
  *own* known result paths (pulled generically off the `RunResult`/
  `ReconcileRunResult` dataclass via `_extract_result_paths()`, matched by
  basename) — `filename` never touches the filesystem directly.
- [x] `WEB-21` `tests/test_webapp_scrape_dispatch.py` — 13 tests: dispatch
  with/without `wait_fn` (verified via a fake provider module, since
  `import_module` is mocked to return it regardless of the real module name
  string passed in), 409 on concurrent same-provider jobs, 404s for unknown
  job/filename, a URL-encoded path-traversal attempt (`..%2F..%2F..%2Fetc%2Fpasswd`)
  rejected, and a real file genuinely served end-to-end. Verified again with a
  real `uvicorn` process (not just `TestClient`) hitting all four route
  families.

**Testing gotcha worth remembering:** `@patch("webapp.app._SCRAPER_MODULES",
...)` combined with `@patch("webapp.app.importlib.import_module")` in the same
test silently breaks the first patch (confirmed via an isolated repro — some
interaction in how `mock.patch` resolves nested targets under the same module
path). Worked around by not patching `_SCRAPER_MODULES` at all: since
`import_module` itself is mocked, it returns the fake module regardless of
which real module name string gets looked up, so the real provider slugs
(`chirp`, `kindle`, ...) can be used directly.

### GUI-Frontend-1 — design system — COMPLETE (2026-07-22)
- [x] `WEB-22` `static/style.css` — full token set (color/spacing/radius/font,
  plus semantic success/warning/danger/info colors for status badges),
  `prefers-color-scheme: dark` + `data-theme` override support, and
  `.card`/`.btn` (`-secondary`/`-danger` variants)/`.badge`
  (`.badge-status-{status}` mapping straight to job status strings, so
  templates never need a Jinja if/elif chain)/`.log-panel`/form field classes.
  Applied to `dashboard.html` now too (`.grid`/`.card`/`.btn`), rather than
  leaving it unstyled until GUI-Frontend-2.
- [x] `WEB-23` `base.html` — `.site-header`/`.site-footer` layout using the
  shared CSS; footer states plainly what the app does and doesn't talk to
  over the network.

### GUI-Frontend-2 — dashboard page — COMPLETE except Reconcile card (2026-07-22)
- [x] `WEB-24` Real per-tool cards (done in GUI-Backend-1) + status badge:
  `_latest_status_per_provider()` picks the most-recently-created job per
  provider from the registry (ties broken by insertion order, since
  `datetime.now()` resolution isn't always fine enough to distinguish two
  jobs created in rapid succession — a real bug caught by a flaky test,
  fixed with `>=` instead of `>`); dashboard passes it to the template, which
  renders `badge-status-{{ status }}` next to the tool name when present.
- [x] `WEB-25` Reconcile card linking to `/reconcile` — COMPLETE (2026-07-24),
  see `GUI-Reconcile-1..3`

### GUI-Frontend-3 — run-a-scraper page — COMPLETE (2026-07-22)
- [x] `WEB-26` `GET /scrape/{provider}` page (404 for unknown/disabled
  providers) with the options form (pages/dry-run/output-dir/no-enrich)
- [x] `WEB-27` `static/app.js` — `EventSource` wiring to the log panel
  (unnamed `data:` = log lines), `fetch()` for start/continue/cancel. Needed
  one backend addition beyond the original ticket scope: `_job_event_stream`
  now emits a named `event: status` whenever `job.status` changes (not just
  at the end), so the page knows when to show/hide the Continue button and
  update the badge without polling — the original design only had a final
  `event: done`, which isn't enough to react to `waiting_for_login` live.
  Also added `GET /scrape/{provider}/jobs/{job_id}` (JSON: status, error,
  download links) for the same reason — SSE's `done` event carries only the
  final status string, not result paths.
- [x] `WEB-28` Manual-login "waiting" state UI + Continue/Cancel buttons —
  shown/hidden via the `status` SSE event
- [x] `WEB-29` Completion summary + download links, populated from the new
  job-detail endpoint once the `done` event fires
- **Not automated-tested**: `app.js` itself. There's no JS test runner in
  this repo (Python-only test stack), and adding one (Node/Jest) for ~130
  lines of vanilla JS would be a disproportionate addition. The Python side
  it depends on (page route, job-detail endpoint, SSE status events) is
  fully covered — 10 new tests in `tests/test_webapp_scrape_page.py`. **Action
  item for you**: click through `/scrape/chirp` (or any enabled provider) in
  a real browser at least once to confirm the JS wiring actually works —
  I verified the page renders and serves correctly via a live `uvicorn`
  process, but never exercised the form-submit → SSE → Continue-button flow
  end-to-end, since that needs a real scrape job (Selenium + your login).

### GUI-Reconcile-1..3 — reconcile job + interactive checkbook-style review — COMPLETE (2026-07-24)
Superseded the original upload-based plan below (kept struck through for
context, not deleted — a real design decision changed mid-flight). Built
directly off a real Rec-5 preview session: manually sanity-checking a
179-book gap list against a real Libib export surfaced several real matches
the automated fuzzy/ISBN matcher missed entirely (wrong/missing platform
tags, edition ISBN mismatches, zero-tag entries) — each only found because a
human recognized a title by eye. The user's framing: treat this like
reconciling a checkbook register against a bank statement — outstanding
items on both sides, cross-referenced and confirmed by a human, not just
scored and auto-assigned.

~~`WEB-30` Multipart upload endpoint~~ — **not built.** Confirmed with the
user: local file-path text fields instead, exactly matching the CLI's own
`--libib`/`--chirp`/etc. flags. This is a `127.0.0.1`-only single-user tool
and the CSVs are already sitting on disk from prior CLI/GUI scrape runs —
shuttling their bytes through an HTTP upload would be pure overhead with no
real benefit here.

- [x] `libib_reconcile/review.py` (new) — the core logic module:
  - `stable_gap_key()`/`stable_libib_key()` — sha1 content hashes over
    *immutable identity fields only* (title/author/isbn/provider), so a
    decision survives the exact edit it tells the user to make (adding a
    tag) without needing a database or row-index tracking.
  - Two JSON files per output directory: `reconcile_{timestamp}_review_
    snapshot.json` (write-once — gap books + their already-computed
    enrichment, plus the full candidate pool of unmatched Libib entries) and
    `reconcile_review_decisions.json` (mutable, un-timestamped, durable
    human judgment, atomic write via temp-file + `os.replace`). No database:
    a few thousand small JSON records is well within "load it all, rewrite
    it all" territory at personal-library scale, and it keeps the project's
    existing all-flat-files philosophy intact — this is the first thing in
    the project that needs durable (restart-surviving) state, unlike the
    job registry's deliberately in-memory, disposable design.
  - `rank_candidates()` — reuses `reconciler.py`'s scoring (`title_score`/
    `author_overlap`, promoted from private) with the automated matcher's
    plausibility gate dropped entirely, since bypassing that gate is the
    whole point.
  - `search_candidates()` — a guaranteed, always-available substring search
    across the *entire* unmatched pool, not just the top suggestions — the
    real answer for titles too dissimilar for any scoring function to rank
    highly (translated/abridged titles, omnibus editions).
  - `finalize_review()` — partitions gap books by decision (confirmed-match
    excluded, **undecided stays in the output CSV by default** — finalizing
    never silently drops anything not explicitly confirmed), reuses
    `output.write_gap_csv()` completely unchanged by reconstructing
    enrichment straight from the snapshot (no re-enrichment, no network
    calls), and writes a new `write_tag_suggestions_report()` — one line per
    confirmed match telling the user exactly which existing Libib entry
    needs which tag added by hand. **Confirmed matches never write back to
    Libib directly** — only ever affect this tool's own output.
- [x] `cancel_fn` threaded through `isbn_enricher.enrich_missing_isbns()` and
  `output.enrich_gap_books()` (previously neither accepted one) and
  `core.run()` — real per-book network calls during a reconcile (ISBN
  resolution + enrichment) are exactly why this needed the same cooperative-
  cancel treatment every scraper's own `resolve_isbns()`/`enrich_books()`
  already has.
- [x] `core.run()` writes the review snapshot as a side effect (only when
  not `dry_run`, same gating as every other output file) and
  `ReconcileRunResult` gained `review_snapshot_path`.
- [x] `webapp/app.py` — reuses `Job`/`JobRegistry`/SSE exactly as-is for the
  "run a reconcile" step (`provider="reconcile"` is just another string key
  to the registry, nothing scraper-specific about it) — genuinely job-shaped
  work since ISBN resolution + enrichment over a few hundred gap books takes
  minutes, not seconds. All new routes are added directly inside
  `create_app()`, deliberately *not* a separate `APIRouter` module — every
  existing route in this app is defined the same inline way, and splitting
  routing structure for reconcile alone would be a second, inconsistent
  pattern for no real benefit at this app's size. New routes: `GET
  /reconcile`, `POST /reconcile/jobs`, `GET /reconcile/jobs/{id}/events`,
  `GET /reconcile/jobs/{id}`, `POST /reconcile/jobs/{id}/cancel`, `GET
  /reconcile/review`, `GET /api/reconcile/review/gaps`, `GET
  /api/reconcile/review/candidates`, `POST /api/reconcile/review/decisions`,
  `POST /reconcile/review/finalize`, `GET /reconcile/review/download` (a
  dedicated download route, not reusing the job-scoped
  `/downloads/{job_id}/{filename}` — finalize output can be regenerated long
  after the originating Job object is gone, including across a server
  restart, which is the entire point of this feature persisting to disk).
- [x] `webapp/templates/reconcile.html` (run form, modeled on `scrape.html`,
  no login-wait section since reconcile has none) and
  `webapp/templates/reconcile_review.html` (two-column checkbook layout —
  searchable gap list with decision badges + progress counter on the left,
  ranked/searchable candidate panel with Confirm/Reject on the right).
  `webapp/static/app.js` gained `initReconcilePage()`/
  `initReconcileReviewPage()` in the same shared script (no per-page JS
  files, matching the existing convention); `style.css` gained a small
  `.review-layout`/`.candidate-row` addition plus three new
  `badge-status-{undecided,confirmed_match,confirmed_new}` variants
  extending the existing status-badge convention.
- [x] Tests: `tests/test_reconcile_review.py` (pure logic — key determinism,
  snapshot round-trip, decisions upsert/atomicity, ranking, search,
  finalize), `tests/test_webapp_reconcile_dispatch.py` (job routes,
  `TestClient`), `tests/test_webapp_reconcile_review.py` (review/decision/
  finalize/download routes, including an explicit test that re-reads the
  decisions JSON straight off disk through a totally independent code path —
  the concrete proof this survives a closed tab or server restart, unlike a
  `Job`'s in-memory status). Plus a real end-to-end smoke run against this
  session's actual Chirp/Kindle/Nook CSVs and Libib export (not mocked),
  confirming the whole pipeline produces a real, loadable snapshot.
- [x] **Follow-up, same session:** per-book on-demand re-enrichment. Prompted
  by the user noticing some gap books carry no metadata at all (a
  `--no-enrich` run, or `enrich_book()`'s Open Library/Google Books/AI/
  Wikidata chain simply finding nothing the first time) — added
  `review.gap_has_enrichment()`/`review.refresh_gap_enrichment()` (a single
  real network call, not job-backed; merges the fresh result field-by-field
  so a retry can never regress already-found data, atomically mutating the
  snapshot in place) plus `POST /api/reconcile/review/enrich`. The gap list
  now flags any book with no metadata (`has_enrichment` on each `/gaps`
  entry) so it's scannable at a glance, and the candidate panel shows a
  metadata summary with a "Try to find more info" button per book.
- [x] **Follow-up, first real full-library review (2026-07-25):** three more
  fixes from actually using the feature against a real 469-gap-book review:
  1. **Real bug: the candidate pool excluded anything already `matched`.**
     Concrete case: "Vision in Silver" (Kobo gap book) couldn't find its real
     Libib entry "Vision In Silver: A Novel of the Others" via search at
     all — that entry had already been auto-matched (possibly wrongly, or
     just to a different platform's copy) and so was invisible to search
     entirely. Since this feature's whole premise is "the algorithm might
     have missed or mismatched something," restricting candidates to only
     what the algorithm *didn't* already decide on was backwards. Fixed:
     `write_review_snapshot()`'s candidate pool now includes every Libib
     entry regardless of match status (only `deleted`/`removed`-tagged
     entries stay excluded) — removed the now-pointless
     `_CANDIDATE_STATUSES` filter entirely.
  2. **User worry: "this is going to take forever, I need to save my
     progress."** Decisions already persist to disk on every click (atomic
     write to `reconcile_review_decisions.json`, keyed by a content hash
     independent of any specific snapshot file) — this was already true,
     just not visible or reassuring enough. Added an explicit regression
     test proving decisions survive regenerating the snapshot from
     identical inputs (same stable keys → same decisions apply), a
     user-facing note on the review page explaining this, and a "Saved ✓"
     flash after every decision/enrichment/metadata save so it's never a
     silent, easy-to-doubt operation.
  3. **"I need a way to put metadata in — as a human, not just AI."** The
     enrich button only ever pulled from automated sources. Added
     `review.set_manual_enrichment()` — unlike the auto-refresh's
     never-regress merge, this sets exactly what the human typed, including
     an intentional blank (clearing a wrong value is a real edit, not noise
     to protect against) — plus `POST /api/reconcile/review/manual-enrichment`
     and editable description/publisher/publish-date/page-count/series
     fields directly in the review page, pre-filled with current values.

### GUI log visibility — root logger level bug — COMPLETE (2026-07-23)
Found while chasing a user report of "no feedback at all" during a Kobo run
that had actually completed successfully. Root cause was much bigger than
the per-book logging granularity fixed just before it (see below): **every
`log.info(...)` call from every scraper, run through the GUI, had been
silently dropped this entire session** — not just the new per-book lines.
`webapp/jobs/log_bridge.py`'s `install()` attaches a `JobLogHandler` to the
*root* logger at webapp startup, before any scraper module is ever imported.
Each scraper's own `logging.basicConfig(level=logging.INFO, ...)` (its
CLI-path setup) becomes a complete no-op once the root logger already has a
handler — Python's `basicConfig()` skips everything it does, including
setting the level, whenever `root.handlers` is non-empty (unless
`force=True`). So the root logger's level silently stayed at Python's
`WARNING` default under the GUI, and every `log.info(...)` call — all
per-page/per-book progress output — never even reached the handler. Only
`log.warning`/`log.error` calls got through, which is exactly why the user
only ever saw rate-limit warnings and errors, never any progress. The CLI
path never hit this, since there a scraper's own `basicConfig()` is the
*first* one to touch the root logger.
- [x] `install()` now also does `root.setLevel(logging.INFO)` explicitly.
- [x] `tests/test_log_bridge.py::test_install_sets_root_level_so_scraper_info_logs_are_not_dropped`
  — regression test; verified it actually fails without the fix (confirmed
  by reproducing the pre-fix behavior standalone) before confirming it
  passes with the fix, so it's a real guard, not a tautology.

### Enrichment reliability — Google Books circuit breaker — COMPLETE (2026-07-23)
Prompted by a user hitting sustained 3/3 rate-limit failures on *every* book
during a real Kindle scrape — jitter and a longer 429 backoff (shipped
earlier the same session) didn't help, because Google's anonymous/no-API-key
Books quota doesn't clear in seconds once tripped; every book was just
re-discovering the same block for the cost of 3 wasted retries each.
- [x] `lib/http_retry.py`'s `request_json()` gained `on_rate_limited` — fires
  the instant a 429 is seen, before the wait, so a caller can react
  immediately rather than only after every retry is exhausted. Also now logs
  the actual response body's error detail (Google's `error.errors[].reason`,
  e.g. `rateLimitExceeded` vs `dailyLimitExceeded`) since `Retry-After` isn't
  being sent by this endpoint and the difference matters (minutes vs a full
  day before it clears).
- [x] `lib/enricher.py` gained a module-level circuit breaker for Google
  Books specifically: `_fetch_google_books_metadata()` now checks
  `_google_books_in_cooldown()` before ever calling out, and
  `_trip_google_books_circuit_breaker()` (wired as `on_rate_limited`) sets a
  5-minute cooldown the moment any book gets 429'd — every subsequent book
  in the run (and later runs, since it's process-wide, matching the actual
  per-IP nature of the limit) skips Google Books instantly instead of
  burning 3 retries rediscovering the same block. `max_retries=1` for the
  Google Books call itself, since retries within one book's request don't
  clear a quota that lasts minutes.
- [x] Fixed a real observability gap found while debugging this: the AI
  (OpenAI) fallback had zero log output on success — only warnings on
  failure — so there was no way to tell from the log whether it was ever
  running. `_fetch_ai_metadata()`/`_fetch_openai_metadata()` now log an
  attempt and a clear success/no-result line.
- [x] Also fixed in the same session: `AI_PROVIDER` matching was
  case-sensitive (`"OpenAI"` in `.env` silently failed to match the
  hardcoded `"openai"` check, logging "Unknown AI_PROVIDER" instead of
  running the fallback) — now `.strip().lower()`-normalized before comparing.
- [x] After the above shipped, the user reported the AI fallback logging an
  attempt but then "returned no usable fields" for real books, with no
  further diagnosis possible — `data.get(field)` all coming back falsy gives
  no hint whether the model returned genuine nulls or just different key
  names than requested. `_fetch_openai_metadata()` now sends
  `response_format: {"type": "json_object"}` (forces valid JSON, reducing
  the risk of a markdown-fenced or narrated response) and logs the raw
  response content when no field matched, so this is diagnosable next time
  instead of a dead end.
- [x] Prompt rework (same session, after the diagnostics above pointed at the
  likely cause): the original prompt told the model to "use null for any
  field you are not confident about," which combined with `temperature=0`
  made it default to null for most fields on most books — backwards for a
  fallback that only ever runs once Open Library *and* Google Books have
  already failed, where any reasonable estimate beats a blank field. Added
  a system message explicitly asking for best-good-faith estimates ("do not
  default to null just because you lack an exact figure"), and made the
  format unambiguous per field instead of leaving "confident" to
  interpretation: `publish_date` is now explicitly year-only (4-digit), and
  `page_count` explicitly allows an approximate/rounded integer rather than
  requiring exact. Not verified against real output yet — the user is
  running a full scrape now; check the new raw-content-on-empty logging
  (added just before this) next session to see whether real books are
  actually getting filled now, or whether prompt tuning needs another pass.
- [x] Fallback order clarified for the user mid-session, worth restating
  here since it's easy to get backwards: **Open Library → Google Books →
  AI**, in that order — AI is the last resort, tried only once both of the
  others have already left a field blank, not tried before Google Books and
  not retried afterward. If AI also comes up empty, the field is just left
  blank; there's no further fallback after it.
- [x] `GOOGLE_BOOKS_API_KEY` (optional) — root-cause fix rather than just
  managing the symptom: the anonymous Google Books endpoint's quota is what
  was actually driving the sustained 429s above, and a free API key (Google
  Cloud Console → enable Books API → Create Credentials → API key, no
  Application restriction — this is a server-side script, not a browser page,
  so HTTP-referrer/mobile-app restrictions don't apply) gets a separate,
  larger quota. `_fetch_google_books_metadata()` adds `key=...` to the
  request params when set; the circuit breaker stays in place either way as
  a safety net. Documented in README + `.env.example`.

### Enrichment reliability — Open Library circuit breaker + identified requests — COMPLETE (2026-07-23)
Prompted by a real 2000+/400+-book Kindle/Chirp run hitting repeated Open
Library 503 ("Service Temporarily Unavailable") storms — several separate
books each burning all 4 retries (~30s) before failing, then more books
hitting the same wall minutes later. Researched Open Library's own published
rate-limit policy and general 429-vs-503 retry practice before changing
anything (see sources below) rather than guessing at a bigger backoff.
- Key finding: Open Library's API docs state unidentified requests are capped
  at 1 request/second, while requests carrying a `User-Agent` identifying the
  app get 3/second — and that unidentified bulk traffic is more likely to be
  throttled/blocked outright. This codebase's Open Library requests (unlike
  the Wikidata ones) were sending no `User-Agent` at all.
- Key finding: the observed 503s are a known, acknowledged Open Library
  backend reliability issue (see internetarchive/openlibrary#6804) — bursty
  and self-clearing over a few minutes, not a hard quota block like Google
  Books'. General practice treats 5xx and 429 differently: 429 needs a longer
  respectful backoff (already handled, see the Google Books entry above); a
  transient 5xx can retry with a shorter delay, but a *sustained* run of them
  means the service is down and every further request should back off
  entirely for a while rather than each independently re-discovering the
  outage.
- [x] `_OL_HEADERS` (`lib/openlibrary.py`) — identifying `User-Agent`, same
  style already used for Wikidata requests — added to every Open Library
  call (`_ol_query`, plus the direct ISBN-edition/works fetches in
  `lib/enricher.py`'s `_fetch_open_library()`, which don't go through
  `_ol_query`).
- [x] `lib/http_retry.py`'s `request_json()` gained `on_exhausted` — fires
  once, only when every retry attempt failed (never on a 404, which is a
  legitimate "not found" answer). Distinct from `on_rate_limited` (which
  fires on the very first 429, before waiting): a single transient 503 that
  succeeds on retry should not trip a breaker, only a request that failed
  outright after every attempt should.
- [x] `lib/openlibrary.py` gained a module-level circuit breaker mirroring
  the Google Books one: `_ol_in_cooldown()`/`_trip_ol_circuit_breaker()`, a
  90-second cooldown (short relative to Google's 5 minutes, since these
  storms have been observed clearing well inside that) wired as
  `_ol_query()`'s `on_exhausted`. `_fetch_open_library()` in
  `lib/enricher.py` also checks the cooldown at entry before making its own
  direct calls, so a tripped breaker skips Open Library entirely (falling
  straight through to Google Books/AI fallback) instead of every subsequent
  book paying the same ~30s retry tax rediscovering the same outage.
- [x] Tests: `tests/test_http_retry.py` (`on_exhausted` fires on exhaustion,
  not on 404 or success), `tests/test_openlibrary.py` (breaker trips/skips/
  resumes), `tests/test_enricher.py` (`_fetch_open_library` skips entirely
  while in cooldown).

Sources consulted: [Open Library API docs](https://openlibrary.org/developers/api)
and [internetarchive/openlibrary#8534](https://github.com/internetarchive/openlibrary/issues/8534)
(rate-limit policy); [internetarchive/openlibrary#6804](https://github.com/internetarchive/openlibrary/issues/6804)
(503 reliability issue); general retry/circuit-breaker practice distinguishing
429 (throttling, longer backoff) from 5xx (transient capacity, shorter
per-attempt backoff but circuit-break on sustained failure) per standard
resilience-pattern guidance (e.g. Grab Engineering's "Designing Resilient
Systems" series).

### Kobo scraper — blank-author selector bug — COMPLETE (2026-07-24)
Found while sanity-checking the Rec-5 preview gap list: 76 of 186 real
Kobo-scraped books (41%) had a blank author, spanning wildly different
authors/publishers (Terry Pratchett's whole Discworld backlist, Ursula K. Le
Guin, several Disney movie tie-ins, "The Secret Garden") — too broad a spread
for these books to genuinely lack author metadata on Kobo's own page, which
pointed at the scraper missing a real DOM variant rather than a data gap.
Per the session's established practice (see the Kindle pagination fix above),
asked for a real DOM sample rather than guessing at a fix.
- Root cause, confirmed against the live DOM the user provided: the author
  selector (`kobo_to_libib/core.py`'s `_SEL_AUTHOR`) was
  `p.authors.product-field a.contributor-name` — requires the contributor
  name to be an `<a>` tag. Kobo only renders it as a link when it has an
  author-search page for that person; otherwise the exact same
  `contributor-name` class is applied to a plain `<span>` instead
  (`<span class="contributor-name">Terry Pratchett</span>`), which the
  `a.`-prefixed selector silently never matched — `find_element` raised,
  was caught, and `author` stayed `""`.
- [x] Fixed by dropping the tag requirement: `_SEL_AUTHOR =
  "p.authors.product-field .contributor-name"` — matches the class
  regardless of whether Kobo rendered it as a link or not.
- Not independently unit-testable: the existing Selenium mocks in
  `tests/test_kobo.py` fake `find_element` by checking whether a keyword
  appears in the selector string, not real CSS tag+class semantics — they
  couldn't have caught this bug in the first place (a mock keyed on
  "contributor" matches `a.contributor-name` and `.contributor-name`
  identically). Verified by full-suite/black/ruff/mypy pass instead; real
  confirmation will come from the next live Kobo rerun.

### GUI-Settings-1 — settings page
- [ ] `WEB-37` Read-only env-var status display (`configured`/`not configured`,
  never the value) for `KINDLE_EMAIL`/`KINDLE_PASSWORD`/`AI_PROVIDER`/`OPENAI_API_KEY`
- [x] `WEB-38a` Kindle's non-interactive `credentials_from_env()` — COMPLETE
  (2026-07-23, prompted by a user hitting exactly this while testing: a
  running server started before `.env` had Kindle credentials fell through
  to `_prompt_credentials()`'s `input()`/`getpass()` fallback, hanging the
  whole server on a stdin prompt the browser has no way to answer, with no
  way to cancel it (blocked before any `wait_fn`/`cancel_fn` checkpoint) —
  required a full process restart to recover. Fix: `kindle_to_libib/core.py`
  gained `credentials_from_env()` (raises `ValueError` immediately with a
  copy-pasteable `KINDLE_EMAIL=`/`KINDLE_PASSWORD=` snippet instead of
  blocking) and a `run(credentials_fn=...)` parameter, defaulting to the
  unchanged CLI behavior (`_prompt_credentials`). `webapp/app.py`'s
  `_build_run_callable` detects `credentials_fn` in `run()`'s signature the
  same way it already detects `wait_fn`/`cancel_fn`, wiring in the module's
  `credentials_from_env` when present. `#job-result-summary` gained
  `white-space: pre-line` so the snippet's newlines actually render. Also
  found and fixed a real test bug while wiring this up: three `test_kindle.py`
  tests patched `_prompt_credentials` and called `run()` with no explicit
  `credentials_fn`, relying on the patch reaching `run()`'s default parameter
  — it can't, since a keyword-only default is bound to the original function
  object at module-definition time, not the (later-patched) module attribute.
  Fixed by passing `credentials_fn=mock_creds` explicitly, matching how other
  tests already inject `wait_fn`.
- [x] `WEB-38` Optional `python-dotenv` `.env` loading at startup — COMPLETE
  (2026-07-23, done ahead of the rest of `GUI-Settings-1`, prompted by a user
  request while testing). `load_dotenv()` at `webapp/app.py` module level
  (covers both `python -m webapp` and `uvicorn webapp.app:app`); `.env.example`
  template added at repo root; `.gitignore` fixed to also exclude a literal
  `.env` file (previously only `.env/`, a directory pattern, was listed —
  almost certainly meant for a stray venv folder, not the dotenv file, which
  would have been an accidental-secret-commit risk once a real `.env` existed).
  CLI-only usage (scrapers run directly, not through the GUI) still requires
  real environment variables — `.env` is a webapp-only convenience per this
  ticket's original scope.

### GUI-Security-1 — hardening pass
- [ ] `WEB-39` Origin/Referer check dependency on all `POST`/`DELETE` routes
- [ ] `WEB-40` Full path-traversal + upload test sweep
- [ ] `WEB-41` Review: binding, credential handling, shutdown cleanup — write up findings

### GUI-Polish-1 — docs
- [ ] `WEB-42` README — new "Web GUI" section: how to run
  (`uvicorn webapp.main:app`), screenshots, security notes
- [ ] `WEB-43` `docs/CLAUDE.md` — webapp architecture section + session history
- [ ] `WEB-44` Finalize `pyproject.toml` coverage source/omit for `webapp`

### New dependencies

`requirements.txt` (runtime — the web app ships as part of the tool):
`fastapi`, `uvicorn[standard]`, `jinja2`, `python-multipart` (required for
FastAPI/Starlette form/file upload parsing), `python-dotenv`.

`requirements-dev.txt`: `httpx` (FastAPI's `TestClient` is httpx-backed).

No Celery/Redis/message broker — the in-memory-registry + thread + SSE design
deliberately avoids needing one for a single-user local tool.

---

## Known Constraints & Edge Cases

### Metadata enrichment — data sources
- **Open Library** is the primary metadata source. Use ISBN lookup where available;
  fall back to title+author search. The Works API (`/works/{id}.json`) and Editions
  endpoint provide description, publisher, publish_date, and page count.
- **Google Books public metadata API** (`/books/v1/volumes?q=isbn:{isbn}`) requires
  no authentication and is used as a fallback when Open Library has no data.
- **Price is not fetched.** No free API has reliable price coverage. If price is
  already present in the row from the scraper, it is preserved as-is.
- **Wikidata SPARQL** is the series source. Query by ISBN-13 (`wdt:P212`) first;
  fall back to title+author. Extract series label (`wdt:P179`) and ordinal (`wdt:P1545`).
  If Wikidata returns a series name but no ordinal, stamp `#ZZZ` so it's easy to find
  and fix manually.

### Series notes format
```
Series: The Dragon Knight #009 || Additional Notes: <original notes>
Series: The Dragon Knight #ZZZ || Additional Notes: <original notes>
```
Position is always zero-padded to 3 digits. If no series data is found, `notes`
is left unchanged and `group` is left blank.

### Google Books enrichment shortcut
The Google Books Library API already returns description, publisher, publishedDate,
and pageCount in the volume metadata. For `google_to_libib` books that have these
fields populated, the enricher skips Open Library and Google Books metadata calls
entirely and only runs the Wikidata series query.

### Kindle scraper status
The `kindle_to_libib` scraper is working correctly. It scrapes library metadata
from the Amazon web page via Selenium and is unaffected by any DRM or epub tooling
(e.g. Epubor). No changes needed.

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
- `bn` (Barnes & Noble's own abbreviation) used instead of `nook` — found live
  (2026-07-24), 6 entries in the real export. Now mapped to `nook` in
  `_PROVIDER_KEYWORDS`/`_DIGITAL_KEYWORDS` — see `docs/CLAUDE.md`'s "Provider
  detection keywords" section.
- Some entries have **no platform tag at all** even though a scrape found the
  book (e.g. an entry tagged only `digital, kindle` for a book also owned on
  Nook) — not fixable in code, since there's no tag to recognize and the
  ISBN commonly differs between a print/Kindle edition and a separately-
  published Nook ebook edition (confirmed live: same title, two different
  ISBNs). Shows up as a `gap` book from the scrape's side even though the
  Libib entry technically already exists — re-running the gap CSV as an
  import would create a **duplicate**, not fix the existing entry. Only a
  human editing the Libib tag can resolve this one; flag it rather than
  auto-import when reviewing a gap list with entries that look suspiciously
  like a title you already own on another platform.

### Matching edge cases
- Series books: "Dune (Dune Chronicles, #1)" vs "Dune" — `_title_is_plausible` handles via word overlap
- Omnibus/box sets: Libib entry "Mistborn Books 1-3" vs three individual entries — will not match; orphan expected
- Duplicate ISBNs in scrape (same book on two platforms) — dedup before matching
- **Untagged entries never got an ISBN check (real bug found and fixed,
  2026-07-24):** `should_skip()` returning `True` (no digital tag at all —
  e.g. added to Libib without ever being tagged) short-circuited the whole
  entry before ISBN-exact matching ever ran, contradicting this project's own
  stated principle of always trying ISBN-exact first. Found live: 46 entries
  in a real export had no tags at all, and 38 of those (83%) had an ISBN on
  file. Fixed by moving the ISBN-exact check ahead of the skip cutoff in
  `reconcile()` — it's authoritative regardless of tags, so running it
  universally can only rescue matches, never produce a false one. Skip
  entries still never get fuzzy-matched (no tags means no provider to scope
  a fuzzy search against anyway).
- **Tie-break determinism (real bug found and fixed, 2026-07-24):** a book
  owned on two scraped platforms with an identical fuzzy title score used to
  resolve to a different provider across otherwise-identical reruns, because
  `find_fuzzy_match()` iterated `entry.providers` — a `set[str]`, whose
  iteration order Python randomizes per process — and only sorted candidates
  by score, so a stable sort preserved whatever order the set happened to
  produce that run. Confirmed live: 179 vs 180 gap books across back-to-back
  runs against the same real export + scrape CSVs. Fixed in
  `libib_reconcile/reconciler.py` by sorting on an explicit
  `(-score, provider, idx)` key instead of score alone, so ties always
  resolve the same way. See `docs/CLAUDE.md`'s reconciler "Matching design"
  section for the full writeup and the regression test name.
