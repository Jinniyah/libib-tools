import time

import pytest

from webapp.jobs.registry import JobAlreadyRunningError, JobRegistry
from webapp.jobs.runner import start_job


def _wait_for(predicate, timeout=2.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# ==========================
# Full lifecycle — fake provider, no Selenium
# ==========================


def test_job_completes_after_manual_login_wait():
    registry = JobRegistry()

    def fake_run(wait_fn, cancel_fn):
        wait_fn()
        return "done"

    job = start_job(registry, "fake", fake_run)

    assert _wait_for(lambda: job.status == "waiting_for_login")

    job.continue_event.set()
    job.thread.join(timeout=2)

    assert job.status == "completed"
    assert job.result == "done"


def test_job_completes_without_login_step():
    """Providers with no manual login (Kindle) never call wait_fn at all."""
    registry = JobRegistry()

    def fake_run(wait_fn, cancel_fn):
        return "done-no-login"

    job = start_job(registry, "fake-kindle-like", fake_run)
    job.thread.join(timeout=2)

    assert job.status == "completed"
    assert job.result == "done-no-login"


def test_job_fails_on_exception():
    registry = JobRegistry()

    def fake_run(wait_fn, cancel_fn):
        raise RuntimeError("boom")

    job = start_job(registry, "fake", fake_run)
    job.thread.join(timeout=2)

    assert job.status == "failed"
    assert job.error == "boom"


def test_job_cancelled_during_login_wait():
    registry = JobRegistry()

    def fake_run(wait_fn, cancel_fn):
        wait_fn()
        return "should never get here"

    job = start_job(registry, "fake", fake_run)
    assert _wait_for(lambda: job.status == "waiting_for_login")

    job.cancel_event.set()
    job.thread.join(timeout=2)

    assert job.status == "cancelled"
    assert job.result is None


# ==========================
# Registry — one active job per provider
# ==========================


def test_registry_blocks_second_job_for_same_provider_while_active():
    registry = JobRegistry()

    def fake_run(wait_fn, cancel_fn):
        wait_fn()
        return "done"

    job = start_job(registry, "fake", fake_run)
    assert _wait_for(lambda: job.status == "waiting_for_login")

    with pytest.raises(JobAlreadyRunningError):
        registry.create("fake")

    # Clean up the still-waiting job.
    job.cancel_event.set()
    job.thread.join(timeout=2)


def test_registry_allows_new_job_after_previous_completes():
    registry = JobRegistry()

    def fake_run(wait_fn, cancel_fn):
        return "done"

    job1 = start_job(registry, "fake", fake_run)
    job1.thread.join(timeout=2)
    assert job1.status == "completed"

    job2 = registry.create("fake")
    assert job2.id != job1.id


def test_registry_allows_concurrent_jobs_for_different_providers():
    registry = JobRegistry()

    def fake_run(wait_fn, cancel_fn):
        wait_fn()
        return "done"

    job_a = start_job(registry, "provider-a", fake_run)
    job_b = start_job(registry, "provider-b", fake_run)

    assert _wait_for(lambda: job_a.status == "waiting_for_login")
    assert _wait_for(lambda: job_b.status == "waiting_for_login")

    job_a.cancel_event.set()
    job_b.cancel_event.set()
    job_a.thread.join(timeout=2)
    job_b.thread.join(timeout=2)


def test_registry_get_and_all():
    registry = JobRegistry()
    job = registry.create("fake")

    assert registry.get(job.id) is job
    assert job in registry.all()
    assert registry.get("does-not-exist") is None
