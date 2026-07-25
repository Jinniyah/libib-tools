from libib_reconcile.libib_reader import LibibEntry
from libib_reconcile.reconciler import _dedupe_scraped_books, reconcile


def _entry(
    title,
    creators="",
    tags=None,
    providers=None,
    ean_isbn13="",
    upc_isbn10="",
    skip=False,
    ambiguous=False,
):
    return LibibEntry(
        title=title,
        creators=creators,
        tags=tags or set(),
        providers=providers or set(),
        ean_isbn13=ean_isbn13,
        upc_isbn10=upc_isbn10,
        skip=skip,
        ambiguous=ambiguous,
    )


# ==========================
# ISBN-exact matching
# ==========================


def test_isbn_exact_match_high_confidence():
    entry = _entry(
        "Dune", "Frank Herbert", providers={"kindle"}, ean_isbn13="9780593135204"
    )
    scraped = {"kindle": [("Dune", "Frank Herbert", "9780593135204", "cover")]}

    result = reconcile([entry], scraped)

    assert len(result.libib_results) == 1
    match = result.libib_results[0]
    assert match.status == "matched"
    assert match.confidence == "high"
    assert match.method == "exact_isbn"
    assert match.provider == "kindle"


def test_isbn_exact_match_upc10():
    entry = _entry("Dune", "Frank Herbert", providers={"kobo"}, upc_isbn10="0593135202")
    scraped = {"kobo": [("Dune", "Frank Herbert", "0593135202", "cover")]}

    result = reconcile([entry], scraped)

    assert result.libib_results[0].status == "matched"
    assert result.libib_results[0].confidence == "high"


def test_isbn_match_is_provider_agnostic():
    """A Libib entry tagged only 'kobo' can still ISBN-match a book found in
    the chirp scrape — ISBN doesn't care what platform the tag says."""
    entry = _entry(
        "Dune", "Frank Herbert", providers={"kobo"}, ean_isbn13="9780593135204"
    )
    scraped = {"chirp": [("Dune", "Frank Herbert", "9780593135204", "cover")]}

    result = reconcile([entry], scraped)

    match = result.libib_results[0]
    assert match.status == "matched"
    assert match.provider == "chirp"
    assert match.confidence == "high"


# ==========================
# Fuzzy matching
# ==========================


def test_fuzzy_match_medium_confidence_with_author_overlap():
    entry = _entry("The Fifth Season", "N.K. Jemisin", providers={"kobo"})
    scraped = {"kobo": [("The Fifth Season", "N. K. Jemisin", None, "cover")]}

    result = reconcile([entry], scraped)

    match = result.libib_results[0]
    assert match.status == "matched"
    assert match.confidence == "medium"
    assert match.method == "fuzzy_title_author"


def test_fuzzy_match_low_confidence_without_author_overlap():
    entry = _entry("The Fifth Season", "N.K. Jemisin", providers={"kobo"})
    scraped = {"kobo": [("The Fifth Season", "Somebody Else", None, "cover")]}

    result = reconcile([entry], scraped)

    match = result.libib_results[0]
    assert match.status == "matched"
    assert match.confidence == "low"
    assert match.method == "title_only"


def test_fuzzy_match_low_confidence_missing_author():
    entry = _entry("The Fifth Season", "", providers={"kobo"})
    scraped = {"kobo": [("The Fifth Season", "N.K. Jemisin", None, "cover")]}

    result = reconcile([entry], scraped)

    assert result.libib_results[0].confidence == "low"


def test_fuzzy_match_scoped_to_tagged_providers():
    """Even a great fuzzy candidate in an untagged provider's pool must not match."""
    entry = _entry("The Fifth Season", "N.K. Jemisin", providers={"kindle"})
    scraped = {"kobo": [("The Fifth Season", "N.K. Jemisin", None, "cover")]}

    result = reconcile([entry], scraped)

    assert result.libib_results[0].status == "libib_only"


def test_no_fuzzy_match_when_title_implausible():
    entry = _entry("The Fifth Season", "N.K. Jemisin", providers={"kobo"})
    scraped = {"kobo": [("Completely Different Book", "Nobody", None, "cover")]}

    result = reconcile([entry], scraped)

    assert result.libib_results[0].status == "libib_only"


def test_greedy_fuzzy_match_picks_best_score():
    entry = _entry("The Fifth Season", "N.K. Jemisin", providers={"kobo"})
    scraped = {
        "kobo": [
            ("The Fifth Seasons", "N.K. Jemisin", None, "close-but-not-exact"),
            ("The Fifth Season", "N.K. Jemisin", None, "exact-title"),
        ]
    }

    result = reconcile([entry], scraped)

    match = result.libib_results[0]
    assert match.book is not None
    assert match.book[3] == "exact-title"


# ==========================
# Provider-aware / cross-format matching
# ==========================


def test_fuzzy_match_tie_break_is_deterministic_not_hash_order_dependent():
    """A book owned on two scraped platforms with an identical title score
    must resolve to the same provider every time. entry.providers is a set,
    so iterating it directly (as find_fuzzy_match once did) let Python's
    per-process string hash randomization decide which provider's candidate
    landed first in a tie — and a stable sort on score alone just preserves
    whichever came first. Confirmed live (2026-07-24): the same real input
    data produced a different gap list across three back-to-back runs.
    Asserting the specific winner (alphabetically-first provider, "chirp"
    over "kobo") pins down the actual deterministic tie-break rule rather
    than just re-running in-process, where the hash seed can't change
    anyway."""
    entry = _entry("Oathblood", "Mercedes Lackey", providers={"chirp", "kobo"})
    scraped = {
        "chirp": [("Oathblood", "Mercedes Lackey", None, "chirp-cover")],
        "kobo": [("Oathblood", "Mercedes Lackey", None, "kobo-cover")],
    }

    match = reconcile([entry], scraped).libib_results[0]

    assert match.provider == "chirp"


def test_multi_provider_entry_matches_either_scrape():
    entry = _entry("Mistborn", "Brandon Sanderson", providers={"kindle", "kobo"})
    scraped = {"kobo": [("Mistborn", "Brandon Sanderson", None, "cover")]}

    result = reconcile([entry], scraped)

    match = result.libib_results[0]
    assert match.status == "matched"
    assert match.provider == "kobo"


def test_cross_format_entry_matches_either_scrape():
    entry = _entry("Piranesi", "Susanna Clarke", providers={"chirp", "kindle", "nook"})
    scraped = {"nook": [("Piranesi", "Susanna Clarke", None, "cover")]}

    result = reconcile([entry], scraped)

    assert result.libib_results[0].status == "matched"
    assert result.libib_results[0].provider == "nook"


# ==========================
# Ambiguous / skip / orphan
# ==========================


def test_ambiguous_entry_resolved_via_isbn():
    entry = _entry(
        "Some Book",
        "Some Author",
        providers={"digital_unknown"},
        ean_isbn13="9780593135204",
        ambiguous=True,
    )
    scraped = {"kindle": [("Some Book", "Some Author", "9780593135204", "cover")]}

    result = reconcile([entry], scraped)

    match = result.libib_results[0]
    assert match.status == "matched"
    assert match.confidence == "high"


def test_ambiguous_entry_without_isbn_stays_ambiguous():
    """Ambiguous entries have no named provider to scope a fuzzy search
    against, so a great title match elsewhere must not rescue them."""
    entry = _entry(
        "Some Book", "Some Author", providers={"digital_unknown"}, ambiguous=True
    )
    scraped = {"kindle": [("Some Book", "Some Author", None, "cover")]}

    result = reconcile([entry], scraped)

    assert result.libib_results[0].status == "ambiguous"


def test_skip_entry_can_still_isbn_match():
    """A skip=True entry (no digital tag at all) can still resolve via
    ISBN-exact — it's authoritative regardless of tags, so there's no
    false-positive risk in trying it. Found live 2026-07-24: 46 entries in a
    real export had no tags at all, and 38 of those had an ISBN on file that
    the old skip-before-ISBN ordering never got a chance to check."""
    entry = _entry(
        "Deleted Book", "Author", providers=set(), ean_isbn13="9780593135204", skip=True
    )
    scraped = {"kindle": [("Deleted Book", "Author", "9780593135204", "cover")]}

    result = reconcile([entry], scraped)

    match = result.libib_results[0]
    assert match.status == "matched"
    assert match.confidence == "high"


def test_skip_entry_without_isbn_match_is_out_of_scope():
    entry = _entry("Deleted Book", "Author", providers=set(), skip=True)

    result = reconcile(
        [entry], {"kindle": [("Something Else", "Nobody", None, "cover")]}
    )

    match = result.libib_results[0]
    assert match.status == "out_of_scope"
    assert match.book is None


def test_skip_entry_does_not_get_fuzzy_matched():
    """Skip entries still must not fall through to fuzzy matching even if
    they happen to carry provider tags — only the provider-agnostic
    ISBN-exact pass is allowed to rescue them."""
    entry = _entry("Some Book", "Some Author", providers={"kindle"}, skip=True)
    scraped = {"kindle": [("Some Book", "Some Author", None, "cover")]}

    result = reconcile([entry], scraped)

    assert result.libib_results[0].status == "out_of_scope"


def test_no_match_found_is_libib_only_orphan():
    entry = _entry("Nonexistent Book", "Nobody", providers={"kindle"})
    result = reconcile([entry], {"kindle": []})
    assert result.libib_results[0].status == "libib_only"


# ==========================
# Scraped-side classification
# ==========================


def test_scraped_book_with_no_match_is_missing_from_libib():
    scraped = {"kindle": [("New Book", "New Author", "9780593135204", "cover")]}
    result = reconcile([], scraped)

    assert len(result.scraped_results) == 1
    assert result.scraped_results[0].status == "missing_from_libib"


def test_matched_scraped_book_is_marked_matched():
    entry = _entry(
        "Dune", "Frank Herbert", providers={"kindle"}, ean_isbn13="9780593135204"
    )
    scraped = {"kindle": [("Dune", "Frank Herbert", "9780593135204", "cover")]}

    result = reconcile([entry], scraped)

    assert result.scraped_results[0].status == "matched"


# ==========================
# One-to-one consumption
# ==========================


def test_one_scraped_book_cannot_satisfy_two_libib_entries():
    entry_a = _entry(
        "Dune", "Frank Herbert", providers={"kindle"}, ean_isbn13="9780593135204"
    )
    entry_b = _entry(
        "Dune Duplicate Tag",
        "Frank Herbert",
        providers={"kindle"},
        ean_isbn13="9780593135204",
    )
    scraped = {"kindle": [("Dune", "Frank Herbert", "9780593135204", "cover")]}

    result = reconcile([entry_a, entry_b], scraped)

    statuses = [m.status for m in result.libib_results]
    assert statuses.count("matched") == 1
    assert "libib_only" in statuses


# ==========================
# Dedup before matching
# ==========================


def test_dedupe_scraped_books_collapses_duplicates_before_matching():
    entry = _entry(
        "Dune", "Frank Herbert", providers={"kindle"}, ean_isbn13="9780593135204"
    )
    scraped = {
        "kindle": [
            ("Dune", "Frank Herbert", "9780593135204", "cover1"),
            ("Dune", "Frank Herbert", "9780593135204", "cover2"),
        ]
    }

    result = reconcile([entry], scraped)

    # Deduped down to one scraped record, and it's matched — not left behind
    # as a phantom "missing_from_libib" duplicate.
    assert len(result.scraped_results) == 1
    assert result.scraped_results[0].status == "matched"


def test_dedupe_scraped_books_helper_preserves_isbn_and_cover():
    books = [
        ("Dune", "Frank Herbert", "9780593135204", "cover1"),
        ("Dune", "Frank Herbert", "9780593135204", "cover1"),
    ]
    result = _dedupe_scraped_books(books)
    assert result == [("Dune", "Frank Herbert", "9780593135204", "cover1")]


def test_dedupe_scraped_books_helper_preserves_cover_when_isbn_missing_for_many():
    """Regression: multiple distinct books that all have isbn=None must each
    keep their own cover, not collide on a shared empty-isbn dict key."""
    books = [
        ("Book A", "Author A", None, "coverA"),
        ("Book B", "Author B", None, "coverB"),
        ("Book C", "Author C", None, "coverC"),
    ]
    result = _dedupe_scraped_books(books)
    covers = {title: cover for title, _, _, cover in result}
    assert covers == {"Book A": "coverA", "Book B": "coverB", "Book C": "coverC"}


# ==========================
# End-to-end mixed scenario
# ==========================


def test_reconcile_mixed_scenario_counts():
    entries = [
        _entry(
            "Dune", "Frank Herbert", providers={"kindle"}, ean_isbn13="9780593135204"
        ),
        _entry("Orphan Book", "Orphan Author", providers={"kobo"}),
        _entry(
            "Ambiguous Book", "Someone", providers={"digital_unknown"}, ambiguous=True
        ),
        _entry("Deleted Entry", "Nobody", providers=set(), skip=True),
    ]
    scraped = {
        "kindle": [("Dune", "Frank Herbert", "9780593135204", "cover")],
        "kobo": [("Unmatched Kobo Book", "Someone Else", None, "cover")],
    }

    result = reconcile(entries, scraped)

    statuses = {m.entry.title: m.status for m in result.libib_results}
    assert statuses == {
        "Dune": "matched",
        "Orphan Book": "libib_only",
        "Ambiguous Book": "ambiguous",
        "Deleted Entry": "out_of_scope",
    }

    scraped_statuses = {b.book[0]: b.status for b in result.scraped_results}
    assert scraped_statuses == {
        "Dune": "matched",
        "Unmatched Kobo Book": "missing_from_libib",
    }
