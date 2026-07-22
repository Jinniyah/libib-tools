# webapp/jobs/log_bridge.py
#
# Bridges each scraper's existing logging.info(...) calls to the right job's
# log_queue, without touching a single one of those call sites. One handler,
# installed on the root logger at app startup, looks up the emitting thread's
# id in a small registry (populated when a job's thread starts, removed when
# it ends) and pushes matching records onto that job's queue. A log record
# from any other thread (e.g. a FastAPI request-handling thread) is a no-op.

from __future__ import annotations

import logging
import threading

from webapp.jobs.registry import Job

_thread_jobs: dict[int, Job] = {}
_lock = threading.Lock()

_FORMAT = "%(asctime)s  %(levelname)-8s  %(message)s"
_DATEFMT = "%H:%M:%S"


def register_thread(job: Job) -> None:
    with _lock:
        _thread_jobs[threading.get_ident()] = job


def unregister_thread() -> None:
    with _lock:
        _thread_jobs.pop(threading.get_ident(), None)


class JobLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        with _lock:
            job = _thread_jobs.get(record.thread) if record.thread is not None else None
        if job is None:
            return
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        job.log_queue.put(message)


def install() -> JobLogHandler:
    """Attach the handler to the root logger. Call once at app startup."""
    handler = JobLogHandler()
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    logging.getLogger().addHandler(handler)
    return handler
