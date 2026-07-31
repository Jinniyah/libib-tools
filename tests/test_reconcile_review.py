import csv
import json
import os
import tempfile
from unittest.mock import patch

import pytest

import libib_reconcile.review as review_module
from lib import EnrichmentResult
from libib_reconcile.libib_reader import LibibEntry
from libib_reconcile.reconciler import MatchResult, ReconcileResult, ScrapedBookResult
from libib_reconcile.review import (
    Decision,
    finalize_orphan_review,
    finalize_review,
    gap_has_enrichment,
    list_orphans,
    list_review_snapshots,
    load_decisions,
    load_orphan_decisions,
    load_review_snapshot,
    rank_candidates,
    rank_orphan_duplicates,
    refresh_gap_enrichment,
    resolved_via_gap_review,
    save_decision,
    save_orphan_decision,
    search_candidates,
    search_orphan_duplicates,
    set_manual_enrichment,
    stable_gap_key,
    stable_libib_key,
    write_review_snapshot,
)

EMPTY = EnrichmentResult()


@pytest.fixture(autouse=True)
def _isolate_global_skip_list(tmp_path, monkeypatch):
    """The global skip list lives at a fixed, real-user path
    (~/.config/libibtools/reconcile_skips.json) by design — redirect it to
    a throwaway location for every test in this file so tests never read or
    write the developer's actual home directory."""
    monkeypatch.setattr(
        review_module, "_GLOBAL_SKIP_LIST_PATH", str(tmp_path / "reconcile_skips.json")
    )


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


def _gap_scraped_result(title, author="", isbn=None, provider="chirp", cover="cover"):
    return ScrapedBookResult(
        provider, (title, author, isbn, cover), "missing_from_libib"
    )


def _write_snapshot(tmp, libib_results, gap_results, enrichment=EMPTY, timestamp="ts"):
    result = ReconcileResult(libib_results=libib_results, scraped_results=[])
    enriched_gap_books = [(r, enrichment) for r in gap_results]
    path = write_review_snapshot(result, enriched_gap_books, {}, tmp, timestamp)
    return path


# ==========================
# stable_gap_key / stable_libib_key
# ==========================


def test_stable_gap_key_same_input_same_key():
    book = ("Dune", "Frank Herbert", "9780593135204", "cover")
    assert stable_gap_key("chirp", book) == stable_gap_key("chirp", book)


def test_stable_gap_key_differs_by_provider():
    book = ("Dune", "Frank Herbert", None, "cover")
    assert stable_gap_key("chirp", book) != stable_gap_key("kobo", book)


def test_stable_gap_key_differs_by_title_author_isbn():
    base = ("Dune", "Frank Herbert", "9780593135204", "cover")
    other_title = ("Dune Messiah", "Frank Herbert", "9780593135204", "cover")
    other_author = ("Dune", "Someone Else", "9780593135204", "cover")
    other_isbn = ("Dune", "Frank Herbert", "0000000000000", "cover")
    keys = {
        stable_gap_key("chirp", base),
        stable_gap_key("chirp", other_title),
        stable_gap_key("chirp", other_author),
        stable_gap_key("chirp", other_isbn),
    }
    assert len(keys) == 4


def test_stable_gap_key_ignores_cover_url():
    a = ("Dune", "Frank Herbert", None, "cover-a")
    b = ("Dune", "Frank Herbert", None, "cover-b")
    assert stable_gap_key("chirp", a) == stable_gap_key("chirp", b)


def test_stable_libib_key_same_input_same_key():
    entry = _entry("Dune", "Frank Herbert", ean_isbn13="9780593135204")
    assert stable_libib_key(entry) == stable_libib_key(entry)


def test_stable_libib_key_ignores_tags_providers_status():
    """A key must survive the exact edit a confirmed decision tells the user
    to make (adding a tag) — otherwise re-tagging silently orphans the
    decision that caused it."""
    before = _entry(
        "Iron Widow", "Xiran Jay Zhao", tags={"kindle"}, providers={"kindle"}
    )
    after = _entry(
        "Iron Widow",
        "Xiran Jay Zhao",
        tags={"kindle", "nook"},
        providers={"kindle", "nook"},
    )
    assert stable_libib_key(before) == stable_libib_key(after)


def test_stable_libib_key_differs_by_title_author_isbn():
    base = _entry("Dune", "Frank Herbert", ean_isbn13="9780593135204")
    other_title = _entry("Dune Messiah", "Frank Herbert", ean_isbn13="9780593135204")
    other_author = _entry("Dune", "Someone Else", ean_isbn13="9780593135204")
    other_isbn = _entry("Dune", "Frank Herbert", ean_isbn13="0000000000000")
    keys = {stable_libib_key(e) for e in (base, other_title, other_author, other_isbn)}
    assert len(keys) == 4


# ==========================
# write_review_snapshot / load_review_snapshot
# ==========================


def test_snapshot_round_trip():
    enrichment = EnrichmentResult(description="A great book.", publisher="Ace")
    libib_results = [
        MatchResult(
            _entry("Orphan Book", "Author A"), None, None, None, None, "libib_only"
        )
    ]
    gap_results = [_gap_scraped_result("Gap Book", "Author B", provider="kobo")]

    with tempfile.TemporaryDirectory() as tmp:
        path = _write_snapshot(
            tmp, libib_results, gap_results, enrichment, "2026-07-24_12-00"
        )
        snapshot = load_review_snapshot(path)

    assert len(snapshot["gap_books"]) == 1
    gap = snapshot["gap_books"][0]
    assert gap["title"] == "Gap Book"
    assert gap["provider"] == "kobo"
    assert gap["enrichment"]["description"] == "A great book."
    assert gap["enrichment"]["publisher"] == "Ace"

    assert len(snapshot["candidate_pool"]) == 1
    candidate = snapshot["candidate_pool"][0]
    assert candidate["title"] == "Orphan Book"
    assert candidate["status"] == "libib_only"


def test_snapshot_candidate_pool_includes_every_match_status():
    """Real bug found live (2026-07-25): a Libib entry the automated matcher
    already claimed ("matched") used to be excluded from the candidate pool
    entirely — so a wrong auto-match, or a book legitimately owned on a
    second platform, could never be found via search. The whole point of
    this feature is catching cases the algorithm got wrong or incomplete,
    so "matched" must be searchable too, not just the three unmatched
    statuses."""
    libib_results = [
        MatchResult(_entry("Orphan"), None, None, None, None, "libib_only"),
        MatchResult(_entry("Ambiguous"), None, None, None, None, "ambiguous"),
        MatchResult(_entry("OutOfScope"), None, None, None, None, "out_of_scope"),
        MatchResult(_entry("Matched"), "kindle", None, "high", "exact_isbn", "matched"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_snapshot(tmp, libib_results, [])
        snapshot = load_review_snapshot(path)

    titles = {c["title"] for c in snapshot["candidate_pool"]}
    assert titles == {"Orphan", "Ambiguous", "OutOfScope", "Matched"}


def test_matched_entry_is_findable_via_search():
    """The concrete "Vision In Silver" scenario: a gap book whose true
    Libib match was already auto-consumed by (possibly the wrong) another
    scraped book must still be searchable and confirmable by a human."""
    libib_results = [
        MatchResult(
            _entry("Vision In Silver: A Novel of the Others", "Anne Bishop"),
            "kindle",
            None,
            "medium",
            "fuzzy_title_author",
            "matched",
        )
    ]
    gap_results = [
        _gap_scraped_result("Vision in Silver", "Anne Bishop", provider="kobo")
    ]

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(tmp, libib_results, gap_results)
        snapshot = load_review_snapshot(snapshot_path)

    results, total = search_candidates(
        "vision in silver",
        snapshot["candidate_pool"],
        {},
        snapshot["gap_books"][0]["key"],
    )
    assert total == 1
    assert results[0]["title"] == "Vision In Silver: A Novel of the Others"


def test_snapshot_excludes_deleted_and_removed_tagged_entries():
    libib_results = [
        MatchResult(
            _entry("Deleted Book", tags={"deleted"}),
            None,
            None,
            None,
            None,
            "out_of_scope",
        ),
        MatchResult(
            _entry("Removed Book", tags={"removed"}),
            None,
            None,
            None,
            None,
            "out_of_scope",
        ),
        MatchResult(_entry("Real Orphan"), None, None, None, None, "libib_only"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_snapshot(tmp, libib_results, [])
        snapshot = load_review_snapshot(path)

    titles = {c["title"] for c in snapshot["candidate_pool"]}
    assert titles == {"Real Orphan"}


# ==========================
# decisions
# ==========================


def test_save_and_load_decision_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        save_decision(tmp, "gap-key-1", "confirmed_match", libib_key="libib-key-1")
        decisions = load_decisions(tmp)

    assert decisions["gap-key-1"].status == "confirmed_match"
    assert decisions["gap-key-1"].libib_key == "libib-key-1"


def test_load_decisions_returns_empty_dict_when_no_file():
    with tempfile.TemporaryDirectory() as tmp:
        assert load_decisions(tmp) == {}


def test_save_decision_undecided_clears_prior_decision():
    with tempfile.TemporaryDirectory() as tmp:
        save_decision(tmp, "gap-key-1", "confirmed_new")
        save_decision(tmp, "gap-key-1", "undecided")
        decisions = load_decisions(tmp)

    assert "gap-key-1" not in decisions


def test_save_decision_is_atomic_no_leftover_temp_file():
    with tempfile.TemporaryDirectory() as tmp:
        save_decision(tmp, "gap-key-1", "confirmed_new")
        files = os.listdir(tmp)

    assert "reconcile_review_decisions.json" in files
    assert not any(f.endswith(".tmp") for f in files)


def test_decisions_persist_across_separate_load_calls():
    """The concrete test of the actual point of this feature: decisions
    aren't tied to any in-memory object's lifetime — reading them back is a
    fresh disk read every time, so they survive a closed tab or a server
    restart just as well as a second save_decision() call in-process."""
    with tempfile.TemporaryDirectory() as tmp:
        save_decision(tmp, "gap-1", "confirmed_new")
        save_decision(tmp, "gap-2", "confirmed_match", libib_key="lib-2")

        # Simulate a totally fresh read, as a new process/request would do.
        reloaded = load_decisions(tmp)

        assert reloaded["gap-1"].status == "confirmed_new"
        assert reloaded["gap-2"].libib_key == "lib-2"

        # And directly off disk, not through any function in this module either.
        with open(
            os.path.join(tmp, "reconcile_review_decisions.json"), encoding="utf-8"
        ) as f:
            raw = json.load(f)
        assert raw["gap-1"]["status"] == "confirmed_new"


# ==========================
# rank_candidates
# ==========================


def _candidate(key, title, creators=""):
    return {
        "key": key,
        "title": title,
        "creators": creators,
        "tags": [],
        "providers": [],
        "ean_isbn13": "",
        "upc_isbn10": "",
        "status": "libib_only",
    }


def _gap(key, title, author=""):
    return {
        "key": key,
        "provider": "chirp",
        "title": title,
        "author": author,
        "isbn": None,
        "cover_url": "",
        "enrichment": {},
    }


def test_rank_candidates_orders_by_score_descending():
    gap = _gap("g1", "The Fifth Season", "N.K. Jemisin")
    pool = [
        _candidate("c1", "Completely Unrelated Title"),
        _candidate("c2", "The Fifth Season", "N.K. Jemisin"),
        _candidate("c3", "The Fifth Seasons"),
    ]
    ranked = rank_candidates(gap, pool, {})
    keys_in_order = [r["candidate"]["key"] for r in ranked]
    assert keys_in_order[0] == "c2"


def test_rank_candidates_respects_score_floor():
    gap = _gap("g1", "The Fifth Season", "N.K. Jemisin")
    pool = [_candidate("c1", "Something Entirely Different And Unrelated")]
    ranked = rank_candidates(gap, pool, {}, score_floor=0.5)
    assert ranked == []


def test_rank_candidates_author_overlap_breaks_score_tie():
    gap = _gap("g1", "Foundation", "Isaac Asimov")
    # Both candidates share an identical title, so title_score ties exactly;
    # only the author-overlap tiebreak should decide the order.
    pool = [
        _candidate("c1", "Foundation", "Somebody Else"),
        _candidate("c2", "Foundation", "Isaac Asimov"),
    ]
    ranked = rank_candidates(gap, pool, {})
    assert ranked[0]["candidate"]["key"] == "c2"
    assert ranked[0]["author_overlap"] is True


def test_rank_candidates_excludes_libib_key_confirmed_to_a_different_gap():
    gap = _gap("g1", "Dune")
    pool = [_candidate("c1", "Dune")]
    decisions = {
        "g2": Decision(status="confirmed_match", libib_key="c1", decided_at="")
    }
    ranked = rank_candidates(gap, pool, decisions)
    assert ranked == []


def test_rank_candidates_still_shows_own_confirmed_match_when_revisited():
    gap = _gap("g1", "Dune")
    pool = [_candidate("c1", "Dune")]
    decisions = {
        "g1": Decision(status="confirmed_match", libib_key="c1", decided_at="")
    }
    ranked = rank_candidates(gap, pool, decisions)
    assert len(ranked) == 1
    assert ranked[0]["candidate"]["key"] == "c1"


# ==========================
# search_candidates
# ==========================


def test_search_candidates_bypasses_score_floor():
    """A title too dissimilar for SequenceMatcher to rank must still be
    findable by a human who knows the exact title to search for."""
    pool = [_candidate("c1", "Some Wildly Different Translated Title")]
    results, total = search_candidates("wildly different", pool, {}, "g1")
    assert total == 1
    assert results[0]["key"] == "c1"


def test_search_candidates_matches_author_too():
    pool = [_candidate("c1", "Some Book", "Distinctive Author Name")]
    results, total = search_candidates("distinctive author", pool, {}, "g1")
    assert total == 1


def test_search_candidates_caps_results_but_reports_total():
    pool = [_candidate(f"c{i}", "Repeated Title") for i in range(60)]
    results, total = search_candidates("repeated", pool, {}, "g1", limit=50)
    assert len(results) == 50
    assert total == 60


def test_search_candidates_excludes_claimed_libib_keys():
    pool = [_candidate("c1", "Dune")]
    decisions = {
        "g2": Decision(status="confirmed_match", libib_key="c1", decided_at="")
    }
    results, total = search_candidates("dune", pool, decisions, "g1")
    assert total == 0


# ==========================
# finalize_review
# ==========================


def test_finalize_excludes_confirmed_match_gap_books_from_csv():
    libib_results = [
        MatchResult(
            _entry("Iron Widow", "Xiran Jay Zhao"), None, None, None, None, "libib_only"
        )
    ]
    gap_results = [_gap_scraped_result("Iron Widow", "Xiran Jay Zhao", provider="nook")]

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(
            tmp, libib_results, gap_results, timestamp="2026-07-24_12-00"
        )
        snapshot = load_review_snapshot(snapshot_path)
        gap_key = snapshot["gap_books"][0]["key"]
        libib_key = snapshot["candidate_pool"][0]["key"]

        save_decision(tmp, gap_key, "confirmed_match", libib_key=libib_key)
        gap_csv_path, tag_report_path = finalize_review(snapshot_path, tmp)

        with open(gap_csv_path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

        assert rows == []
        assert tag_report_path is not None
        with open(tag_report_path, encoding="utf-8") as f:
            text = f.read()
        assert "nook" in text
        assert "Iron Widow" in text


def test_finalize_undecided_gap_book_stays_in_csv_by_default():
    gap_results = [_gap_scraped_result("Never Reviewed", "Author", provider="kobo")]

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(
            tmp, [], gap_results, timestamp="2026-07-24_12-00"
        )
        gap_csv_path, tag_report_path = finalize_review(snapshot_path, tmp)

        with open(gap_csv_path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

    assert len(rows) == 1
    assert rows[0]["title"] == "Never Reviewed"
    assert tag_report_path is None


def test_finalize_excludes_skipped_gap_books_from_csv_with_no_tag_suggestion():
    """A "skipped" decision (library loan, short story — genuinely not
    wanted in Libib) must drop the book from the reviewed gap CSV, same as
    confirmed_match, but must NOT generate a tag suggestion — there's no
    Libib entry to tag, unlike a real match."""
    gap_results = [_gap_scraped_result("A Library Loan", "Author", provider="kobo")]

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(
            tmp, [], gap_results, timestamp="2026-07-27_12-00"
        )
        snapshot = load_review_snapshot(snapshot_path)
        gap_key = snapshot["gap_books"][0]["key"]

        save_decision(tmp, gap_key, "skipped")
        gap_csv_path, tag_report_path = finalize_review(snapshot_path, tmp)

        with open(gap_csv_path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

    assert rows == []
    assert tag_report_path is None


def test_finalize_is_re_runnable_and_non_destructive():
    """finalize_review must be safely callable multiple times as more
    decisions accumulate, without clobbering earlier finalize output."""
    gap_results = [_gap_scraped_result("Some Book", "Author", provider="kobo")]

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(
            tmp, [], gap_results, timestamp="2026-07-24_12-00"
        )

        first_csv, _ = finalize_review(snapshot_path, tmp)
        second_csv, _ = finalize_review(snapshot_path, tmp)

        assert os.path.exists(first_csv)
        assert os.path.exists(second_csv)


# ==========================
# Global skip list — cross-session, cross-output-dir "skipped" memory
# ==========================


def test_skipping_a_gap_records_it_in_the_global_skip_list():
    gap_results = [_gap_scraped_result("A Library Loan", "Author", provider="kobo")]

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(tmp, [], gap_results, timestamp="ts")
        gap_key = load_review_snapshot(snapshot_path)["gap_books"][0]["key"]

        save_decision(tmp, gap_key, "skipped")

    with open(review_module._GLOBAL_SKIP_LIST_PATH, encoding="utf-8") as f:
        global_skips = json.load(f)
    assert gap_key in global_skips


def test_undoing_a_skip_removes_it_from_the_global_skip_list():
    gap_results = [_gap_scraped_result("A Library Loan", "Author", provider="kobo")]

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(tmp, [], gap_results, timestamp="ts")
        gap_key = load_review_snapshot(snapshot_path)["gap_books"][0]["key"]

        save_decision(tmp, gap_key, "skipped")
        save_decision(tmp, gap_key, "undecided")

    with open(review_module._GLOBAL_SKIP_LIST_PATH, encoding="utf-8") as f:
        global_skips = json.load(f)
    assert gap_key not in global_skips


def test_confirming_a_match_does_not_touch_the_global_skip_list():
    gap_results = [_gap_scraped_result("Some Book", "Author", provider="kobo")]

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(tmp, [], gap_results, timestamp="ts")
        gap_key = load_review_snapshot(snapshot_path)["gap_books"][0]["key"]

        save_decision(tmp, gap_key, "confirmed_new")

    assert not os.path.exists(review_module._GLOBAL_SKIP_LIST_PATH)


def test_new_session_pre_labels_a_gap_previously_skipped_elsewhere():
    """The actual point of this feature: a book skipped in one output
    directory must come back already marked "skipped" in a brand-new
    session that uses a completely different output directory — e.g. a
    fresh dated folder — the moment it's re-scraped and reappears as a gap
    with the same identity (same title/author/isbn/provider)."""
    gap_results = [_gap_scraped_result("A Library Loan", "Author", provider="kobo")]

    with tempfile.TemporaryDirectory() as old_session:
        old_snapshot_path = _write_snapshot(
            old_session, [], gap_results, timestamp="ts-old"
        )
        gap_key = load_review_snapshot(old_snapshot_path)["gap_books"][0]["key"]
        save_decision(old_session, gap_key, "skipped")

    with tempfile.TemporaryDirectory() as new_session:
        new_snapshot_path = _write_snapshot(
            new_session, [], gap_results, timestamp="ts-new"
        )
        new_decisions = load_decisions(new_session)
        assert load_review_snapshot(new_snapshot_path)["gap_books"][0]["key"] == gap_key

    assert new_decisions[gap_key].status == "skipped"


def test_new_session_never_overwrites_an_existing_decision_for_that_key():
    """Global pre-labeling must not clobber a decision this exact output
    directory already recorded for the same key — e.g. from a manual
    confirm made before the global list happened to catch up."""
    gap_results = [_gap_scraped_result("A Library Loan", "Author", provider="kobo")]

    with tempfile.TemporaryDirectory() as old_session:
        old_snapshot_path = _write_snapshot(
            old_session, [], gap_results, timestamp="ts-old"
        )
        gap_key = load_review_snapshot(old_snapshot_path)["gap_books"][0]["key"]
        save_decision(old_session, gap_key, "skipped")

    with tempfile.TemporaryDirectory() as new_session:
        save_decision(new_session, gap_key, "confirmed_new")
        _write_snapshot(new_session, [], gap_results, timestamp="ts-new")
        new_decisions = load_decisions(new_session)

    assert new_decisions[gap_key].status == "confirmed_new"


# ==========================
# gap_has_enrichment / refresh_gap_enrichment
# ==========================


def test_gap_has_enrichment_false_for_empty_result():
    gap = _gap("g1", "Some Book")
    gap["enrichment"] = {}
    assert gap_has_enrichment(gap) is False


def test_gap_has_enrichment_true_when_any_field_present():
    gap = _gap("g1", "Some Book")
    gap["enrichment"] = {"description": "A book about things."}
    assert gap_has_enrichment(gap) is True


@patch("libib_reconcile.review.enrich_book")
def test_refresh_gap_enrichment_fetches_and_persists(mock_enrich_book):
    mock_enrich_book.return_value = EnrichmentResult(
        description="Freshly fetched.", publisher="Ace"
    )
    gap_results = [_gap_scraped_result("Some Book", "Some Author", provider="kobo")]

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(
            tmp, [], gap_results, timestamp="2026-07-24_12-00"
        )
        gap_key = load_review_snapshot(snapshot_path)["gap_books"][0]["key"]

        updated = refresh_gap_enrichment(snapshot_path, gap_key)

        assert updated["enrichment"]["description"] == "Freshly fetched."
        assert updated["enrichment"]["publisher"] == "Ace"

        # Persisted, not just returned — a fresh load sees the same data.
        reloaded = load_review_snapshot(snapshot_path)
        assert (
            reloaded["gap_books"][0]["enrichment"]["description"] == "Freshly fetched."
        )

    mock_enrich_book.assert_called_once_with(
        "Some Book", "Some Author", None, None, "cover"
    )


@patch("libib_reconcile.review.enrich_book")
def test_refresh_gap_enrichment_merge_never_regresses_existing_data(mock_enrich_book):
    """A retry that finds less than before (flaky provider, AI_PROVIDER
    unset this time, ...) must not blank out data a prior enrichment pass
    already found."""
    mock_enrich_book.return_value = EnrichmentResult(
        description=None, publisher="New Publisher"
    )
    gap_results = [_gap_scraped_result("Some Book", "Some Author", provider="kobo")]
    original_enrichment = EnrichmentResult(
        description="Original description.", publisher="Old Publisher"
    )

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(
            tmp,
            [],
            gap_results,
            enrichment=original_enrichment,
            timestamp="2026-07-24_12-00",
        )
        gap_key = load_review_snapshot(snapshot_path)["gap_books"][0]["key"]

        updated = refresh_gap_enrichment(snapshot_path, gap_key)

    assert updated["enrichment"]["description"] == "Original description."
    assert updated["enrichment"]["publisher"] == "New Publisher"


def test_refresh_gap_enrichment_unknown_key_raises_key_error():
    gap_results = [_gap_scraped_result("Some Book", provider="kobo")]
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(
            tmp, [], gap_results, timestamp="2026-07-24_12-00"
        )
        with pytest.raises(KeyError):
            refresh_gap_enrichment(snapshot_path, "not-a-real-key")


# ==========================
# Decisions survive a regenerated snapshot (the real worry behind
# "I need a way to save my progress so I can come back")
# ==========================


def test_decisions_survive_regenerating_the_snapshot_from_the_same_inputs():
    """A user's fear, worth pinning down explicitly: does re-running
    reconcile (e.g. to pick up the candidate-pool fix above, or a rescrape)
    wipe out review progress already made? It must not, as long as the same
    output_dir is reused and the underlying book data hasn't changed —
    stable_gap_key()/stable_libib_key() are pure content hashes, so an
    identical rerun reproduces identical keys, and decisions (keyed by that
    hash, stored in a file separate from the timestamped snapshot) apply
    just as well to a brand-new snapshot as to the one that was open when
    the decision was made."""
    gap_results = [_gap_scraped_result("Some Book", "Some Author", provider="kobo")]

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_a = _write_snapshot(tmp, [], gap_results, timestamp="2026-07-25_09-00")
        gap_key = load_review_snapshot(snapshot_a)["gap_books"][0]["key"]
        save_decision(tmp, gap_key, "confirmed_new")

        # Regenerate from the identical inputs, as a real re-run would —
        # different timestamp, same output_dir, same book data.
        snapshot_b = _write_snapshot(tmp, [], gap_results, timestamp="2026-07-25_10-30")
        assert snapshot_b != snapshot_a

        decisions = load_decisions(tmp)
        new_gap_key = load_review_snapshot(snapshot_b)["gap_books"][0]["key"]

    assert new_gap_key == gap_key
    assert decisions[new_gap_key].status == "confirmed_new"


# ==========================
# set_manual_enrichment — human-entered metadata, not just AI/automated
# ==========================


def test_set_manual_enrichment_applies_given_fields():
    gap_results = [_gap_scraped_result("Some Book", "Some Author", provider="kobo")]

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(
            tmp, [], gap_results, timestamp="2026-07-25_09-00"
        )
        gap_key = load_review_snapshot(snapshot_path)["gap_books"][0]["key"]

        updated = set_manual_enrichment(
            snapshot_path,
            gap_key,
            {
                "publisher": "Ace Books",
                "series_name": "The Vorkosigan Saga",
                "series_position": 6,
            },
        )

        assert updated["enrichment"]["publisher"] == "Ace Books"
        assert updated["enrichment"]["series_name"] == "The Vorkosigan Saga"
        assert updated["enrichment"]["series_position"] == 6

        # Persisted, not just returned.
        reloaded = load_review_snapshot(snapshot_path)
        assert reloaded["gap_books"][0]["enrichment"]["publisher"] == "Ace Books"


def test_set_manual_enrichment_can_deliberately_clear_a_field():
    """Unlike the automated refresh's never-regress merge, a human clearing
    a field on purpose (an empty string) must actually clear it — this is
    an intentional edit, not noise to protect against."""
    gap_results = [_gap_scraped_result("Some Book", provider="kobo")]
    original = EnrichmentResult(description="A wrong, auto-generated blurb.")

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(
            tmp, [], gap_results, enrichment=original, timestamp="2026-07-25_09-00"
        )
        gap_key = load_review_snapshot(snapshot_path)["gap_books"][0]["key"]

        updated = set_manual_enrichment(snapshot_path, gap_key, {"description": ""})

    assert updated["enrichment"]["description"] == ""


def test_set_manual_enrichment_ignores_fields_not_in_the_allowed_set():
    gap_results = [_gap_scraped_result("Some Book", provider="kobo")]
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(
            tmp, [], gap_results, timestamp="2026-07-25_09-00"
        )
        gap_key = load_review_snapshot(snapshot_path)["gap_books"][0]["key"]

        updated = set_manual_enrichment(
            snapshot_path,
            gap_key,
            {"isbn13": "9999999999999", "publisher": "Real Field"},
        )

    assert (
        "isbn13" not in updated["enrichment"] or updated["enrichment"]["isbn13"] is None
    )
    assert updated["enrichment"]["publisher"] == "Real Field"


def test_set_manual_enrichment_unknown_key_raises_key_error():
    gap_results = [_gap_scraped_result("Some Book", provider="kobo")]
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(
            tmp, [], gap_results, timestamp="2026-07-25_09-00"
        )
        with pytest.raises(KeyError):
            set_manual_enrichment(snapshot_path, "not-a-real-key", {"publisher": "X"})


# ==========================
# list_review_snapshots — resuming a review after closing the tab
# (or restarting the server) without keeping the exact snapshot path
# ==========================


def test_list_review_snapshots_returns_empty_for_nonexistent_dir():
    assert list_review_snapshots(r"C:\definitely\not\a\real\path") == []


def test_list_review_snapshots_finds_and_summarizes_snapshot_files():
    gap_results = [_gap_scraped_result("Some Book", provider="kobo")]
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(
            tmp, [], gap_results, timestamp="2026-07-25_09-00"
        )
        summaries = list_review_snapshots(tmp)

    assert len(summaries) == 1
    assert summaries[0]["path"] == snapshot_path
    assert summaries[0]["gap_count"] == 1
    assert summaries[0]["decided_count"] == 0


def test_list_review_snapshots_reflects_decided_count():
    gap_results = [
        _gap_scraped_result("Book A", provider="kobo"),
        _gap_scraped_result("Book B", provider="kobo"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(
            tmp, [], gap_results, timestamp="2026-07-25_09-00"
        )
        gap_key = load_review_snapshot(snapshot_path)["gap_books"][0]["key"]
        save_decision(tmp, gap_key, "confirmed_new")

        summaries = list_review_snapshots(tmp)

    assert summaries[0]["gap_count"] == 2
    assert summaries[0]["decided_count"] == 1


def test_list_review_snapshots_sorted_newest_first():
    with tempfile.TemporaryDirectory() as tmp:
        older_path = os.path.join(
            tmp, "reconcile_2026-07-24_09-00_review_snapshot.json"
        )
        newer_path = os.path.join(
            tmp, "reconcile_2026-07-25_09-00_review_snapshot.json"
        )
        for path, generated_at in [
            (older_path, "2026-07-24T09:00:00"),
            (newer_path, "2026-07-25T09:00:00"),
        ]:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "generated_at": generated_at,
                        "source_paths": {},
                        "gap_books": [],
                        "candidate_pool": [],
                    },
                    f,
                )

        summaries = list_review_snapshots(tmp)

    assert [s["path"] for s in summaries] == [newer_path, older_path]


def test_list_review_snapshots_skips_unparseable_files():
    with tempfile.TemporaryDirectory() as tmp:
        bad_path = os.path.join(tmp, "reconcile_2026-07-24_09-00_review_snapshot.json")
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write("not valid json{{{")

        summaries = list_review_snapshots(tmp)

    assert summaries == []


def test_list_review_snapshots_ignores_non_snapshot_files():
    gap_results = [_gap_scraped_result("Some Book", provider="kobo")]
    with tempfile.TemporaryDirectory() as tmp:
        _write_snapshot(tmp, [], gap_results, timestamp="2026-07-25_09-00")
        with open(
            os.path.join(tmp, "reconcile_review_decisions.json"), "w", encoding="utf-8"
        ) as f:
            json.dump({}, f)
        with open(
            os.path.join(tmp, "reconcile_2026-07-25_09-00_gap.csv"),
            "w",
            encoding="utf-8",
        ) as f:
            f.write("title\n")

        summaries = list_review_snapshots(tmp)

    assert len(summaries) == 1


# ==========================
# Orphan review — the mirror image: Libib entries no scrape matched
# ==========================


def test_list_orphans_returns_only_libib_only_status():
    libib_results = [
        MatchResult(_entry("Orphan"), None, None, None, None, "libib_only"),
        MatchResult(_entry("Ambiguous"), None, None, None, None, "ambiguous"),
        MatchResult(_entry("OutOfScope"), None, None, None, None, "out_of_scope"),
        MatchResult(_entry("Matched"), "kindle", None, "high", "exact_isbn", "matched"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = _write_snapshot(tmp, libib_results, [])
        snapshot = load_review_snapshot(path)

    titles = {o["title"] for o in list_orphans(snapshot)}
    assert titles == {"Orphan"}


def test_save_and_load_orphan_decision_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        save_orphan_decision(tmp, "orphan-key-1", "duplicate", libib_key="libib-key-1")
        decisions = load_orphan_decisions(tmp)

    assert decisions["orphan-key-1"].status == "duplicate"
    assert decisions["orphan-key-1"].libib_key == "libib-key-1"


def test_load_orphan_decisions_returns_empty_dict_when_no_file():
    with tempfile.TemporaryDirectory() as tmp:
        assert load_orphan_decisions(tmp) == {}


def test_save_orphan_decision_undecided_clears_prior_decision():
    with tempfile.TemporaryDirectory() as tmp:
        save_orphan_decision(tmp, "orphan-key-1", "needs_archive")
        save_orphan_decision(tmp, "orphan-key-1", "undecided")
        decisions = load_orphan_decisions(tmp)

    assert "orphan-key-1" not in decisions


def test_orphan_decisions_use_a_separate_file_from_gap_decisions():
    """Gap-book and orphan decisions are keyed in different hash spaces
    (stable_gap_key vs stable_libib_key) and reviewed on separate pages —
    they must not collide or overwrite each other even if a hash happened
    to coincide."""
    with tempfile.TemporaryDirectory() as tmp:
        save_decision(tmp, "shared-key", "confirmed_new")
        save_orphan_decision(tmp, "shared-key", "needs_archive")

        gap_decisions = load_decisions(tmp)
        orphan_decisions = load_orphan_decisions(tmp)

        assert gap_decisions["shared-key"].status == "confirmed_new"
        assert orphan_decisions["shared-key"].status == "needs_archive"
        assert os.path.exists(os.path.join(tmp, "reconcile_review_decisions.json"))
        assert os.path.exists(os.path.join(tmp, "reconcile_orphan_decisions.json"))


def test_resolved_via_gap_review_returns_libib_keys_confirmed_from_gap_side():
    gap_decisions = {
        "g1": Decision(status="confirmed_match", libib_key="c1", decided_at=""),
        "g2": Decision(status="confirmed_new", libib_key=None, decided_at=""),
        "g3": Decision(status="skipped", libib_key=None, decided_at=""),
    }
    assert resolved_via_gap_review(gap_decisions) == {"c1"}


def test_rank_orphan_duplicates_orders_by_score_descending():
    orphan = _candidate("o1", "The Fifth Season", "N.K. Jemisin")
    pool = [
        orphan,
        _candidate("c1", "Completely Unrelated Title"),
        _candidate("c2", "The Fifth Season", "N.K. Jemisin"),
        _candidate("c3", "The Fifth Seasons"),
    ]
    ranked = rank_orphan_duplicates(orphan, pool)
    keys_in_order = [r["candidate"]["key"] for r in ranked]
    assert keys_in_order[0] == "c2"


def test_rank_orphan_duplicates_excludes_itself():
    orphan = _candidate("o1", "Dune")
    pool = [orphan, _candidate("c1", "Dune")]
    ranked = rank_orphan_duplicates(orphan, pool)
    keys = [r["candidate"]["key"] for r in ranked]
    assert "o1" not in keys
    assert "c1" in keys


def test_search_orphan_duplicates_excludes_only_self():
    """Unlike search_candidates(), a candidate already claimed elsewhere is
    still a valid duplicate target — two Libib records for the same book,
    where only one is actually still owned, is exactly the scenario this
    feature exists to catch."""
    orphan = _candidate("o1", "Dune")
    pool = [orphan, _candidate("c1", "Dune")]
    results, total = search_orphan_duplicates("dune", pool, "o1")
    assert total == 1
    assert results[0]["key"] == "c1"


def test_finalize_orphan_review_returns_none_when_nothing_decided():
    libib_results = [MatchResult(_entry("Orphan"), None, None, None, None, "libib_only")]
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(tmp, libib_results, [], timestamp="ts")
        assert finalize_orphan_review(snapshot_path, tmp) is None


def test_finalize_orphan_review_writes_duplicate_and_archive_lines():
    libib_results = [
        MatchResult(
            _entry("Iron Widow", "Xiran Jay Zhao"), None, None, None, None, "libib_only"
        ),
        MatchResult(
            _entry("Iron Widow (dup)", "Xiran Jay Zhao"),
            None,
            None,
            None,
            None,
            "libib_only",
        ),
        MatchResult(
            _entry("Stale Loan", "Author"), None, None, None, None, "libib_only"
        ),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(
            tmp, libib_results, [], timestamp="2026-07-30_09-00"
        )
        snapshot = load_review_snapshot(snapshot_path)
        orphans = list_orphans(snapshot)
        real = next(o for o in orphans if o["title"] == "Iron Widow")
        dup = next(o for o in orphans if o["title"] == "Iron Widow (dup)")
        stale = next(o for o in orphans if o["title"] == "Stale Loan")

        save_orphan_decision(tmp, dup["key"], "duplicate", libib_key=real["key"])
        save_orphan_decision(tmp, stale["key"], "needs_archive")

        report_path = finalize_orphan_review(snapshot_path, tmp)

        assert report_path is not None
        with open(report_path, encoding="utf-8") as f:
            text = f.read()

    assert (
        'Archive (duplicate of "Iron Widow" by Xiran Jay Zhao): '
        '"Iron Widow (dup)" by Xiran Jay Zhao' in text
    )
    assert 'Archive (no longer owned): "Stale Loan" by Author' in text


def test_finalize_orphan_review_keep_produces_no_line():
    libib_results = [
        MatchResult(_entry("Fine As-Is"), None, None, None, None, "libib_only")
    ]
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(tmp, libib_results, [], timestamp="ts")
        orphan_key = list_orphans(load_review_snapshot(snapshot_path))[0]["key"]

        save_orphan_decision(tmp, orphan_key, "keep")
        report_path = finalize_orphan_review(snapshot_path, tmp)

    assert report_path is None


def test_finalize_orphan_review_is_re_runnable():
    libib_results = [
        MatchResult(_entry("Stale Loan"), None, None, None, None, "libib_only")
    ]
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _write_snapshot(tmp, libib_results, [], timestamp="ts")
        orphan_key = list_orphans(load_review_snapshot(snapshot_path))[0]["key"]
        save_orphan_decision(tmp, orphan_key, "needs_archive")

        first = finalize_orphan_review(snapshot_path, tmp)
        second = finalize_orphan_review(snapshot_path, tmp)

        assert os.path.exists(first)
        assert os.path.exists(second)
