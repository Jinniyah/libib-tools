import logging
import threading

from webapp.jobs.log_bridge import (
    JobLogHandler,
    install,
    register_thread,
    unregister_thread,
)
from webapp.jobs.registry import JobRegistry


def _make_job(provider: str):
    registry = JobRegistry()
    return registry.create(provider)


def test_log_from_registered_thread_lands_in_that_jobs_queue():
    job = _make_job("fake")
    handler = JobLogHandler()
    log = logging.getLogger("test_log_bridge.isolated_a")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    # Importing webapp.app (elsewhere in the suite) installs its own
    # JobLogHandler on the root logger as a side effect. _thread_jobs is
    # shared module state, so without this, propagation would let that
    # handler double-deliver into the same job's queue.
    log.propagate = False

    def worker():
        register_thread(job)
        try:
            log.info("hello from job thread")
        finally:
            unregister_thread()

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2)

    assert job.log_queue.get(timeout=1) == "hello from job thread"
    log.removeHandler(handler)


def test_log_from_unregistered_thread_is_a_no_op():
    job = _make_job("fake")
    handler = JobLogHandler()
    log = logging.getLogger("test_log_bridge.isolated_b")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False

    log.info("no job registered for this thread")

    assert job.log_queue.empty()
    log.removeHandler(handler)


def test_thread_isolation_job_a_logs_never_leak_into_job_b():
    job_a = _make_job("fake-a")
    job_b = _make_job("fake-b")
    handler = JobLogHandler()
    log = logging.getLogger("test_log_bridge.isolated_c")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False

    def worker(job, message):
        register_thread(job)
        try:
            log.info(message)
        finally:
            unregister_thread()

    t_a = threading.Thread(target=worker, args=(job_a, "message for A"))
    t_b = threading.Thread(target=worker, args=(job_b, "message for B"))
    t_a.start()
    t_a.join(timeout=2)
    t_b.start()
    t_b.join(timeout=2)

    assert job_a.log_queue.get(timeout=1) == "message for A"
    assert job_a.log_queue.empty()
    assert job_b.log_queue.get(timeout=1) == "message for B"
    assert job_b.log_queue.empty()

    log.removeHandler(handler)


def test_unregister_is_safe_when_never_registered():
    # Must not raise even if called on a thread that never registered.
    unregister_thread()


def test_install_sets_root_level_so_scraper_info_logs_are_not_dropped():
    """Regression test for a real bug: each scraper module's own
    logging.basicConfig(level=logging.INFO, ...) call (its CLI-path setup)
    silently no-ops once the root logger already has a handler attached —
    Python's basicConfig() skips everything, including setting the level,
    whenever root.handlers is non-empty (unless force=True). Since install()
    runs at webapp startup, before any scraper module is ever imported,
    every scraper's basicConfig() call became a no-op under the GUI, leaving
    the root logger at Python's WARNING default — every log.info(...) call
    (all per-page/per-book progress output) was silently dropped before it
    ever reached a handler. install() must set the level itself so a logger
    that never configures its own level (matching every scraper's actual
    logger) still has INFO enabled via inheritance.
    """
    install()

    log = logging.getLogger("test_log_bridge.never_configured_by_this_test")
    assert log.isEnabledFor(logging.INFO)
