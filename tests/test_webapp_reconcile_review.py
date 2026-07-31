# TestClient tests for the review-browsing/decision/finalize/download
# routes, against a small hand-built snapshot (via
# libib_reconcile.review.write_review_snapshot() over fabricated
# MatchResult/ScrapedBookResult data — same fixture style as
# test_reconcile_core.py, not the real multi-thousand-row export).

import json
import os
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import libib_reconcile.review as review_module
from lib import EnrichmentResult
from libib_reconcile.libib_reader import LibibEntry
from libib_reconcile.reconciler import MatchResult, ReconcileResult, ScrapedBookResult
from libib_reconcile.review import (
    load_decisions,
    load_review_snapshot,
    write_review_snapshot,
)
from webapp.app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_global_skip_list(tmp_path, monkeypatch):
    """See the identical fixture in test_reconcile_review.py — the global
    skip list lives at a fixed real-user path by design; redirect it here
    too, since this file also exercises write_review_snapshot() and the
    decisions route (which calls save_decision() under the hood)."""
    monkeypatch.setattr(
        review_module, "_GLOBAL_SKIP_LIST_PATH", str(tmp_path / "reconcile_skips.json")
    )


def _entry(title, creators=""):
    return LibibEntry(
        title=title,
        creators=creators,
        tags=set(),
        providers=set(),
        ean_isbn13="",
        upc_isbn10="",
        skip=False,
        ambiguous=False,
    )


def _build_snapshot(tmp):
    libib_results = [
        MatchResult(
            _entry("Iron Widow", "Xiran Jay Zhao"), None, None, None, None, "libib_only"
        )
    ]
    gap_results = [
        ScrapedBookResult(
            "nook",
            ("Iron Widow", "Xiran Jay Zhao", None, "cover"),
            "missing_from_libib",
        )
    ]
    result = ReconcileResult(libib_results=libib_results, scraped_results=[])
    enriched_gap_books = [(r, EnrichmentResult()) for r in gap_results]
    return write_review_snapshot(
        result, enriched_gap_books, {}, tmp, "2026-07-24_12-00"
    )


# ==========================
# GET /reconcile/review
# ==========================


def test_review_page_renders():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        response = client.get("/reconcile/review", params={"snapshot": snapshot_path})
    assert response.status_code == 200


# ==========================
# GET /api/reconcile/review/gaps
# ==========================


def test_review_gaps_returns_gap_list_with_no_decision():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        response = client.get(
            "/api/reconcile/review/gaps", params={"snapshot": snapshot_path}
        )
    assert response.status_code == 200
    gaps = response.json()["gaps"]
    assert len(gaps) == 1
    assert gaps[0]["title"] == "Iron Widow"
    assert gaps[0]["decision"] is None


def test_review_gaps_reflects_saved_decision():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        gap_key = client.get(
            "/api/reconcile/review/gaps", params={"snapshot": snapshot_path}
        ).json()["gaps"][0]["key"]

        client.post(
            "/api/reconcile/review/decisions",
            params={"snapshot": snapshot_path},
            json={"gap_key": gap_key, "status": "confirmed_new"},
        )

        response = client.get(
            "/api/reconcile/review/gaps", params={"snapshot": snapshot_path}
        )
    assert response.json()["gaps"][0]["decision"]["status"] == "confirmed_new"


# ==========================
# GET /api/reconcile/review/candidates
# ==========================


def test_review_candidates_ranked_mode():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        gap_key = client.get(
            "/api/reconcile/review/gaps", params={"snapshot": snapshot_path}
        ).json()["gaps"][0]["key"]

        response = client.get(
            "/api/reconcile/review/candidates",
            params={"snapshot": snapshot_path, "gap_key": gap_key},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "ranked"
    assert data["candidates"][0]["candidate"]["title"] == "Iron Widow"


def test_review_candidates_search_mode():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        gap_key = client.get(
            "/api/reconcile/review/gaps", params={"snapshot": snapshot_path}
        ).json()["gaps"][0]["key"]

        response = client.get(
            "/api/reconcile/review/candidates",
            params={"snapshot": snapshot_path, "gap_key": gap_key, "q": "iron"},
        )
    data = response.json()
    assert data["mode"] == "search"
    assert data["candidates"][0]["title"] == "Iron Widow"


def test_review_candidates_unknown_gap_key_404():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        response = client.get(
            "/api/reconcile/review/candidates",
            params={"snapshot": snapshot_path, "gap_key": "not-a-real-key"},
        )
    assert response.status_code == 404


# ==========================
# POST /api/reconcile/review/decisions — persistence-survives-restart proof
# ==========================


def test_decision_persists_to_disk_independent_of_any_in_memory_object():
    """The concrete test of this feature's actual point: a saved decision
    must be readable straight off disk by a totally independent code path
    (libib_reconcile.review.load_decisions, not anything from the request
    that saved it), proving it isn't tied to the webapp process's in-memory
    state the way a Job's status is."""
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        gap_key = client.get(
            "/api/reconcile/review/gaps", params={"snapshot": snapshot_path}
        ).json()["gaps"][0]["key"]
        libib_key = client.get(
            "/api/reconcile/review/candidates",
            params={"snapshot": snapshot_path, "gap_key": gap_key},
        ).json()["candidates"][0]["candidate"]["key"]

        response = client.post(
            "/api/reconcile/review/decisions",
            params={"snapshot": snapshot_path},
            json={
                "gap_key": gap_key,
                "status": "confirmed_match",
                "libib_key": libib_key,
            },
        )
        assert response.status_code == 200

        decisions_path = os.path.join(tmp, "reconcile_review_decisions.json")
        with open(decisions_path, encoding="utf-8") as f:
            raw = json.load(f)
        assert raw[gap_key]["status"] == "confirmed_match"
        assert raw[gap_key]["libib_key"] == libib_key

        reloaded = load_decisions(tmp)
        assert reloaded[gap_key].status == "confirmed_match"


# ==========================
# POST /reconcile/review/finalize
# ==========================


def test_finalize_returns_download_links():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        response = client.post(
            "/reconcile/review/finalize", params={"snapshot": snapshot_path}
        )
    assert response.status_code == 200
    data = response.json()
    assert data["gap_csv"]["filename"].endswith(".csv")
    assert "/reconcile/review/download" in data["gap_csv"]["url"]


def test_finalize_confirmed_match_produces_tag_report():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        gap_key = client.get(
            "/api/reconcile/review/gaps", params={"snapshot": snapshot_path}
        ).json()["gaps"][0]["key"]
        libib_key = client.get(
            "/api/reconcile/review/candidates",
            params={"snapshot": snapshot_path, "gap_key": gap_key},
        ).json()["candidates"][0]["candidate"]["key"]

        client.post(
            "/api/reconcile/review/decisions",
            params={"snapshot": snapshot_path},
            json={
                "gap_key": gap_key,
                "status": "confirmed_match",
                "libib_key": libib_key,
            },
        )

        response = client.post(
            "/reconcile/review/finalize", params={"snapshot": snapshot_path}
        )
    assert response.json()["tag_report"] is not None


# ==========================
# GET /reconcile/review/download
# ==========================


def test_review_download_serves_finalize_output():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        finalize_data = client.post(
            "/reconcile/review/finalize", params={"snapshot": snapshot_path}
        ).json()

        response = client.get(
            "/reconcile/review/download",
            params={"dir": tmp, "filename": finalize_data["gap_csv"]["filename"]},
        )
    assert response.status_code == 200
    assert "Iron Widow" in response.text


def test_review_download_rejects_filename_not_matching_pattern():
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "secrets.txt"), "w", encoding="utf-8") as f:
            f.write("shh")

        response = client.get(
            "/reconcile/review/download", params={"dir": tmp, "filename": "secrets.txt"}
        )
    assert response.status_code == 404


def test_review_download_rejects_path_traversal():
    with tempfile.TemporaryDirectory() as tmp:
        response = client.get(
            "/reconcile/review/download",
            params={"dir": tmp, "filename": "../../../etc/reconcile_passwd.csv"},
        )
    assert response.status_code == 404


def test_review_download_rejects_dir_escape_via_separator_in_filename():
    """Even a filename that itself matches the safe-character pattern must
    not be allowed to reach outside `dir` via an embedded path separator."""
    with tempfile.TemporaryDirectory() as tmp:
        outside = tempfile.mkdtemp()
        try:
            with open(
                os.path.join(outside, "reconcile_secret.csv"), "w", encoding="utf-8"
            ) as f:
                f.write("secret")

            response = client.get(
                "/reconcile/review/download",
                params={"dir": tmp, "filename": f"..{os.sep}reconcile_secret.csv"},
            )
            assert response.status_code == 404
        finally:
            import shutil

            shutil.rmtree(outside, ignore_errors=True)


def test_review_download_unknown_file_404():
    with tempfile.TemporaryDirectory() as tmp:
        response = client.get(
            "/reconcile/review/download",
            params={"dir": tmp, "filename": "reconcile_2026-07-24_gap_reviewed.csv"},
        )
    assert response.status_code == 404


# ==========================
# GET /api/reconcile/review/gaps — has_enrichment field
# ==========================


def test_review_gaps_reports_has_enrichment_false_for_empty_result():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        response = client.get(
            "/api/reconcile/review/gaps", params={"snapshot": snapshot_path}
        )
    assert response.json()["gaps"][0]["has_enrichment"] is False


# ==========================
# POST /api/reconcile/review/enrich
# ==========================


@patch("libib_reconcile.review.enrich_book")
def test_enrich_fetches_and_persists_metadata(mock_enrich_book):
    mock_enrich_book.return_value = EnrichmentResult(
        description="Freshly fetched.", publisher="Tor"
    )

    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        gap_key = client.get(
            "/api/reconcile/review/gaps", params={"snapshot": snapshot_path}
        ).json()["gaps"][0]["key"]

        response = client.post(
            "/api/reconcile/review/enrich",
            params={"snapshot": snapshot_path},
            json={"gap_key": gap_key},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["has_enrichment"] is True
        assert data["gap"]["enrichment"]["description"] == "Freshly fetched."

        # Persisted to the snapshot on disk, not just returned in the response.
        reloaded = load_review_snapshot(snapshot_path)
        assert reloaded["gap_books"][0]["enrichment"]["publisher"] == "Tor"


@patch("libib_reconcile.review.enrich_book")
def test_enrich_unknown_gap_key_returns_404(mock_enrich_book):
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        response = client.post(
            "/api/reconcile/review/enrich",
            params={"snapshot": snapshot_path},
            json={"gap_key": "not-a-real-key"},
        )
    assert response.status_code == 404
    mock_enrich_book.assert_not_called()


# ==========================
# POST /api/reconcile/review/manual-enrichment
# ==========================


def test_manual_enrichment_saves_human_entered_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        gap_key = client.get(
            "/api/reconcile/review/gaps", params={"snapshot": snapshot_path}
        ).json()["gaps"][0]["key"]

        response = client.post(
            "/api/reconcile/review/manual-enrichment",
            params={"snapshot": snapshot_path},
            json={
                "gap_key": gap_key,
                "publisher": "Tor Books",
                "series_name": "Iron Widow",
                "series_position": 1,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["has_enrichment"] is True
        assert data["gap"]["enrichment"]["publisher"] == "Tor Books"
        assert data["gap"]["enrichment"]["series_position"] == 1

        reloaded = load_review_snapshot(snapshot_path)
        assert reloaded["gap_books"][0]["enrichment"]["series_name"] == "Iron Widow"


def test_manual_enrichment_unknown_gap_key_returns_404():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        response = client.post(
            "/api/reconcile/review/manual-enrichment",
            params={"snapshot": snapshot_path},
            json={"gap_key": "not-a-real-key", "publisher": "X"},
        )
    assert response.status_code == 404


# ==========================
# GET /api/reconcile/review/snapshots — resuming after a closed tab
# ==========================


def test_list_snapshots_route_finds_a_real_snapshot():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        response = client.get("/api/reconcile/review/snapshots", params={"dir": tmp})

    assert response.status_code == 200
    snapshots = response.json()["snapshots"]
    assert len(snapshots) == 1
    assert snapshots[0]["path"] == snapshot_path
    assert snapshots[0]["gap_count"] == 1


def test_list_snapshots_route_empty_for_unknown_dir():
    response = client.get(
        "/api/reconcile/review/snapshots", params={"dir": r"C:\not\a\real\dir"}
    )
    assert response.status_code == 200
    assert response.json()["snapshots"] == []


# ==========================
# Orphan review routes — the mirror image of the gap-review routes above
# ==========================


def _build_two_orphan_snapshot(tmp):
    """Two libib_only entries sharing a title, so a duplicate-search/rank
    has something real to find — same fixture style as _build_snapshot()."""
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
    ]
    result = ReconcileResult(libib_results=libib_results, scraped_results=[])
    return write_review_snapshot(result, [], {}, tmp, "2026-07-30_09-00")


def test_orphans_page_renders():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        response = client.get(
            "/reconcile/review/orphans", params={"snapshot": snapshot_path}
        )
    assert response.status_code == 200


def test_orphans_route_returns_orphan_list_with_no_decision():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        response = client.get(
            "/api/reconcile/review/orphans", params={"snapshot": snapshot_path}
        )
    assert response.status_code == 200
    orphans = response.json()["orphans"]
    assert len(orphans) == 1
    assert orphans[0]["title"] == "Iron Widow"
    assert orphans[0]["decision"] is None
    assert orphans[0]["resolved_via_gap_review"] is False


def test_orphans_route_reflects_saved_decision():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        orphan_key = client.get(
            "/api/reconcile/review/orphans", params={"snapshot": snapshot_path}
        ).json()["orphans"][0]["key"]

        client.post(
            "/api/reconcile/review/orphan-decisions",
            params={"snapshot": snapshot_path},
            json={"orphan_key": orphan_key, "status": "needs_archive"},
        )

        response = client.get(
            "/api/reconcile/review/orphans", params={"snapshot": snapshot_path}
        )
    assert response.json()["orphans"][0]["decision"]["status"] == "needs_archive"


def test_orphans_route_flags_resolved_via_gap_review():
    """An orphan already linked to a gap book from the gap-review side (the
    matcher missed a real match) must show as resolved without a separate
    orphan decision."""
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        gap_key = client.get(
            "/api/reconcile/review/gaps", params={"snapshot": snapshot_path}
        ).json()["gaps"][0]["key"]
        libib_key = client.get(
            "/api/reconcile/review/candidates",
            params={"snapshot": snapshot_path, "gap_key": gap_key},
        ).json()["candidates"][0]["candidate"]["key"]

        client.post(
            "/api/reconcile/review/decisions",
            params={"snapshot": snapshot_path},
            json={
                "gap_key": gap_key,
                "status": "confirmed_match",
                "libib_key": libib_key,
            },
        )

        response = client.get(
            "/api/reconcile/review/orphans", params={"snapshot": snapshot_path}
        )
    orphan = next(o for o in response.json()["orphans"] if o["key"] == libib_key)
    assert orphan["resolved_via_gap_review"] is True


def test_orphan_candidates_ranked_mode():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_two_orphan_snapshot(tmp)
        orphans = client.get(
            "/api/reconcile/review/orphans", params={"snapshot": snapshot_path}
        ).json()["orphans"]
        orphan_key = next(o["key"] for o in orphans if o["title"] == "Iron Widow (dup)")

        response = client.get(
            "/api/reconcile/review/orphan-candidates",
            params={"snapshot": snapshot_path, "orphan_key": orphan_key},
        )
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "ranked"
    assert data["candidates"][0]["candidate"]["title"] == "Iron Widow"


def test_orphan_candidates_search_mode():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_two_orphan_snapshot(tmp)
        orphans = client.get(
            "/api/reconcile/review/orphans", params={"snapshot": snapshot_path}
        ).json()["orphans"]
        orphan_key = next(o["key"] for o in orphans if o["title"] == "Iron Widow (dup)")

        response = client.get(
            "/api/reconcile/review/orphan-candidates",
            params={"snapshot": snapshot_path, "orphan_key": orphan_key, "q": "iron"},
        )
    data = response.json()
    assert data["mode"] == "search"
    assert data["candidates"][0]["title"] == "Iron Widow"


def test_orphan_candidates_unknown_orphan_key_404():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        response = client.get(
            "/api/reconcile/review/orphan-candidates",
            params={"snapshot": snapshot_path, "orphan_key": "not-a-real-key"},
        )
    assert response.status_code == 404


def test_orphan_decision_persists_to_disk_in_its_own_file():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_two_orphan_snapshot(tmp)
        orphans = client.get(
            "/api/reconcile/review/orphans", params={"snapshot": snapshot_path}
        ).json()["orphans"]
        dup_key = next(o["key"] for o in orphans if o["title"] == "Iron Widow (dup)")
        real_key = next(o["key"] for o in orphans if o["title"] == "Iron Widow")

        response = client.post(
            "/api/reconcile/review/orphan-decisions",
            params={"snapshot": snapshot_path},
            json={"orphan_key": dup_key, "status": "duplicate", "libib_key": real_key},
        )
        assert response.status_code == 200

        decisions_path = os.path.join(tmp, "reconcile_orphan_decisions.json")
        with open(decisions_path, encoding="utf-8") as f:
            raw = json.load(f)
    assert raw[dup_key]["status"] == "duplicate"
    assert raw[dup_key]["libib_key"] == real_key


def test_finalize_orphans_returns_null_report_when_nothing_decided():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_snapshot(tmp)
        response = client.post(
            "/reconcile/review/finalize-orphans", params={"snapshot": snapshot_path}
        )
    assert response.status_code == 200
    assert response.json()["report"] is None


def test_finalize_orphans_returns_download_link_after_decision():
    with tempfile.TemporaryDirectory() as tmp:
        snapshot_path = _build_two_orphan_snapshot(tmp)
        orphans = client.get(
            "/api/reconcile/review/orphans", params={"snapshot": snapshot_path}
        ).json()["orphans"]
        dup_key = next(o["key"] for o in orphans if o["title"] == "Iron Widow (dup)")
        real_key = next(o["key"] for o in orphans if o["title"] == "Iron Widow")

        client.post(
            "/api/reconcile/review/orphan-decisions",
            params={"snapshot": snapshot_path},
            json={"orphan_key": dup_key, "status": "duplicate", "libib_key": real_key},
        )

        response = client.post(
            "/reconcile/review/finalize-orphans", params={"snapshot": snapshot_path}
        )
        assert response.status_code == 200
        report = response.json()["report"]
        assert report is not None
        assert report["filename"].endswith("_orphan_review.txt")

        download = client.get(
            "/reconcile/review/download",
            params={"dir": tmp, "filename": report["filename"]},
        )
    assert download.status_code == 200
    assert "Iron Widow (dup)" in download.text
