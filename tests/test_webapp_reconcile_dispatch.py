# Mirrors tests/test_webapp_scrape_dispatch.py's conventions: mock the
# underlying run() function directly (webapp.app.reconcile_core.run) rather
# than exercising a real reconcile, and poll job.status via _wait_for since
# jobs run in a background thread.

import time
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from webapp.app import app, registry

client = TestClient(app)


def _wait_for(predicate, timeout=2.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _fake_reconcile_run(**kwargs):
    return SimpleNamespace(
        gap_csv_path="/tmp/fake_gap.csv",
        summary_path="/tmp/fake_summary.txt",
        orphan_path=None,
        low_confidence_path=None,
        ambiguous_path=None,
        total_libib_entries=10,
        total_gap_books=2,
        review_snapshot_path="/tmp/fake_review_snapshot.json",
        received_cancel_fn=kwargs.get("cancel_fn") is not None,
    )


@patch("webapp.app.reconcile_core.run")
def test_reconcile_job_start_and_completes(mock_run):
    mock_run.side_effect = _fake_reconcile_run

    response = client.post(
        "/reconcile/jobs",
        json={"libib_path": "libib.csv", "chirp": "chirp.csv", "output_dir": "/tmp"},
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    job = registry.get(job_id)
    assert _wait_for(lambda: job.status == "completed")
    assert job.result.review_snapshot_path == "/tmp/fake_review_snapshot.json"
    assert job.result.received_cancel_fn is True

    mock_run.assert_called_once()
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs["libib_path"] == "libib.csv"
    assert call_kwargs["chirp"] == "chirp.csv"
    assert call_kwargs["kindle"] is None
    assert call_kwargs["wait_for_rate_limits"] is False


@patch("webapp.app.reconcile_core.run")
def test_reconcile_job_threads_wait_for_rate_limits_option(mock_run):
    mock_run.side_effect = _fake_reconcile_run

    response = client.post(
        "/reconcile/jobs",
        json={
            "libib_path": "libib.csv",
            "chirp": "chirp.csv",
            "wait_for_rate_limits": True,
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    job = registry.get(job_id)
    assert _wait_for(lambda: job.status == "completed")

    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs["wait_for_rate_limits"] is True


@patch("webapp.app.reconcile_core.run")
def test_reconcile_job_conflict_when_already_running(mock_run):
    def slow_run(**kwargs):
        cancel_fn = kwargs["cancel_fn"]
        while not cancel_fn():
            time.sleep(0.02)
        return _fake_reconcile_run(**kwargs)

    mock_run.side_effect = slow_run

    first = client.post(
        "/reconcile/jobs", json={"libib_path": "libib.csv", "chirp": "chirp.csv"}
    )
    assert first.status_code == 200
    job = registry.get(first.json()["job_id"])
    assert _wait_for(lambda: job.status == "running")

    second = client.post(
        "/reconcile/jobs", json={"libib_path": "libib.csv", "chirp": "chirp.csv"}
    )
    assert second.status_code == 409

    job.cancel_event.set()
    job.thread.join(timeout=2)


@patch("webapp.app.reconcile_core.run")
def test_reconcile_job_detail_includes_review_snapshot_path(mock_run):
    mock_run.side_effect = _fake_reconcile_run

    response = client.post(
        "/reconcile/jobs", json={"libib_path": "libib.csv", "chirp": "chirp.csv"}
    )
    job_id = response.json()["job_id"]
    job = registry.get(job_id)
    assert _wait_for(lambda: job.status == "completed")

    detail = client.get(f"/reconcile/jobs/{job_id}").json()
    assert detail["review_snapshot_path"] == "/tmp/fake_review_snapshot.json"
    assert detail["status"] == "completed"


def test_reconcile_job_detail_unknown_job_returns_404():
    response = client.get("/reconcile/jobs/does-not-exist")
    assert response.status_code == 404


@patch("webapp.app.reconcile_core.run")
def test_reconcile_job_cancel(mock_run):
    def slow_run(**kwargs):
        cancel_fn = kwargs["cancel_fn"]
        while not cancel_fn():
            time.sleep(0.02)
        from lib import OperationCancelled

        raise OperationCancelled()

    mock_run.side_effect = slow_run

    response = client.post(
        "/reconcile/jobs", json={"libib_path": "libib.csv", "chirp": "chirp.csv"}
    )
    job_id = response.json()["job_id"]
    job = registry.get(job_id)
    assert _wait_for(lambda: job.status == "running")

    cancel_response = client.post(f"/reconcile/jobs/{job_id}/cancel")
    assert cancel_response.status_code == 200
    job.thread.join(timeout=2)
    assert job.status == "cancelled"


@patch("webapp.app.reconcile_core.run")
def test_reconcile_job_cancel_already_terminal_returns_409(mock_run):
    mock_run.side_effect = _fake_reconcile_run

    response = client.post(
        "/reconcile/jobs", json={"libib_path": "libib.csv", "chirp": "chirp.csv"}
    )
    job_id = response.json()["job_id"]
    job = registry.get(job_id)
    assert _wait_for(lambda: job.status == "completed")

    cancel_response = client.post(f"/reconcile/jobs/{job_id}/cancel")
    assert cancel_response.status_code == 409
