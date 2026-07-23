import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from webapp.app import app, registry
from webapp.jobs.runner import start_job

client = TestClient(app)


def _wait_for(predicate, timeout=2.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ==========================
# /continue
# ==========================


def test_continue_unblocks_a_waiting_job():
    def fake_run(wait_fn, cancel_fn):
        wait_fn()
        return "done"

    job = start_job(registry, "continue-happy", fake_run)
    assert _wait_for(lambda: job.status == "waiting_for_login")

    response = client.post(f"/scrape/continue-happy/jobs/{job.id}/continue")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert _wait_for(lambda: job.status == "completed")


def test_continue_on_job_not_waiting_returns_409():
    job = registry.create("continue-not-waiting")  # status: queued
    response = client.post(f"/scrape/continue-not-waiting/jobs/{job.id}/continue")
    assert response.status_code == 409


def test_continue_unknown_job_returns_404():
    response = client.post("/scrape/continue-404/jobs/does-not-exist/continue")
    assert response.status_code == 404


def test_continue_provider_mismatch_returns_404():
    job = registry.create("continue-mismatch")
    response = client.post(f"/scrape/other-provider/jobs/{job.id}/continue")
    assert response.status_code == 404


# ==========================
# /cancel
# ==========================


def test_cancel_during_login_wait_marks_job_cancelled():
    def fake_run(wait_fn, cancel_fn):
        wait_fn()
        return "should not get here"

    job = start_job(registry, "cancel-happy", fake_run)
    assert _wait_for(lambda: job.status == "waiting_for_login")

    response = client.post(f"/scrape/cancel-happy/jobs/{job.id}/cancel")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert _wait_for(lambda: job.status == "cancelled")


def test_cancel_already_finished_job_returns_409():
    def fake_run(wait_fn, cancel_fn):
        return "done"

    job = start_job(registry, "cancel-finished", fake_run)
    job.thread.join(timeout=2)
    assert job.status == "completed"

    response = client.post(f"/scrape/cancel-finished/jobs/{job.id}/cancel")
    assert response.status_code == 409


def test_cancel_unknown_job_returns_404():
    response = client.post("/scrape/cancel-404/jobs/does-not-exist/cancel")
    assert response.status_code == 404


def test_cancel_provider_mismatch_returns_404():
    job = registry.create("cancel-mismatch")
    response = client.post(f"/scrape/other-provider/jobs/{job.id}/cancel")
    assert response.status_code == 404


# ==========================
# Shutdown hook — cancels jobs still waiting on login (Tier 1)
# ==========================


def test_shutdown_cancels_jobs_waiting_for_login():
    def fake_run(wait_fn, cancel_fn):
        wait_fn()
        return "should not get here"

    job = start_job(registry, "shutdown-cancel", fake_run)
    assert _wait_for(lambda: job.status == "waiting_for_login")

    with TestClient(app):
        pass  # __exit__ triggers the FastAPI shutdown/lifespan teardown

    assert _wait_for(lambda: job.status == "cancelled")


# ==========================
# /shutdown, /api/jobs-live
# ==========================


def test_jobs_live_true_while_a_job_is_running():
    def fake_run(wait_fn, cancel_fn):
        wait_fn()
        return "done"

    job = start_job(registry, "jobs-live-check", fake_run)
    assert _wait_for(lambda: job.status == "waiting_for_login")

    response = client.get("/api/jobs-live")
    assert response.json() == {"jobs_running": True}

    job.cancel_event.set()
    job.thread.join(timeout=2)


@patch("webapp.app.os._exit")
@patch("webapp.app.threading.Timer")
def test_shutdown_cancels_live_jobs_and_reports_them(mock_timer, mock_exit):
    def fake_run(wait_fn, cancel_fn):
        wait_fn()
        return "should not get here"

    job = start_job(registry, "shutdown-route", fake_run)
    assert _wait_for(lambda: job.status == "waiting_for_login")

    response = client.post("/shutdown")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "jobs_running": True}
    assert job.cancel_event.is_set()
    mock_timer.assert_called_once()
    mock_exit.assert_not_called()  # only called via the (mocked) Timer callback

    job.thread.join(timeout=2)


@patch("webapp.app.os._exit")
@patch("webapp.app.threading.Timer")
def test_shutdown_schedules_process_exit(mock_timer, mock_exit):
    # Not asserting jobs_running here — other test modules in this session
    # may have left dangling non-terminal jobs in the shared registry (see
    # docs/CLAUDE.md's "shared global test state" note), so only the parts
    # of the contract this test actually owns are checked.
    response = client.post("/shutdown")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    mock_timer.assert_called_once()
    mock_exit.assert_not_called()
