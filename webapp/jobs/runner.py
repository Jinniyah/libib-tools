# webapp/jobs/runner.py
#
# Spawns a job's work in a real OS thread. Selenium's Python bindings are
# synchronous end-to-end (no await-able API), so running a scrape inside an
# `async def` route body would block the whole event loop — including the SSE
# stream telling the browser what's happening. This is a legitimate use of a
# background thread precisely because the work is CPU/IO-blocking and driven
# by a third-party synchronous library, not a case for asyncio.

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from webapp.jobs.log_bridge import register_thread, unregister_thread
from webapp.jobs.registry import Job, JobRegistry

log = logging.getLogger(__name__)

# How often the login-wait loop wakes up to check for cancellation.
_WAIT_POLL_INTERVAL = 0.2


class JobCancelled(Exception):
    """Raised internally to unwind out of a cancelled job's wait_fn."""


def _make_wait_fn(job: Job) -> Callable[[], None]:
    """Build the wait_fn a scraper's run() calls during manual login.

    Flips the job to waiting_for_login and polls continue_event (set by the
    browser's "Continue" button — see GUI-Backend-4) until it fires, checking
    cancel_event on every poll so a job stuck at login can still be cancelled.
    """

    def wait_fn() -> None:
        job.status = "waiting_for_login"
        while not job.continue_event.wait(timeout=_WAIT_POLL_INTERVAL):
            if job.cancel_event.is_set():
                raise JobCancelled()
        job.continue_event.clear()
        job.status = "running"

    return wait_fn


def _run(job: Job, run_callable: Callable[[Callable[[], None]], Any]) -> None:
    register_thread(job)
    try:
        job.status = "running"
        wait_fn = _make_wait_fn(job)
        try:
            job.result = run_callable(wait_fn)
            job.status = "completed"
        except JobCancelled:
            job.status = "cancelled"
            log.info("Job %s (%s) cancelled.", job.id, job.provider)
        except Exception as exc:
            job.error = str(exc)
            job.status = "failed"
            log.exception("Job %s (%s) failed.", job.id, job.provider)
    finally:
        unregister_thread()


def start_job(
    registry: JobRegistry,
    provider: str,
    run_callable: Callable[[Callable[[], None]], Any],
) -> Job:
    """Create and start a job.

    `run_callable` receives the job's wait_fn and should pass it through to
    wherever a scraper's run() expects one — e.g.
    `lambda wait_fn: chirp_to_libib.core.run(wait_fn=wait_fn, ...)`. Providers
    with no manual-login step (Kindle) can just ignore the argument.
    """
    job = registry.create(provider)
    thread = threading.Thread(target=_run, args=(job, run_callable), daemon=True)
    job.thread = thread
    thread.start()
    return job
