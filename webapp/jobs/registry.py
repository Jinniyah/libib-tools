# webapp/jobs/registry.py
#
# In-memory job tracking — deliberately not SQLite/Celery/Redis. This is a
# single-user, single-process, local tool; losing in-flight job history on a
# server restart is an acceptable, explicit tradeoff, not an oversight. See
# docs/backlog.md, "Web GUI (webapp)" > Architecture summary.

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

# Statuses that count as "still going" for the one-active-job-per-provider guard.
_LIVE_STATUSES = frozenset({"queued", "waiting_for_login", "running"})


class JobAlreadyRunningError(Exception):
    """Raised when a job is requested for a provider that already has a live job."""


@dataclass
class Job:
    id: str
    provider: str
    status: str
    created_at: datetime
    log_queue: "queue.Queue[str]"
    continue_event: threading.Event
    cancel_event: threading.Event
    thread: Optional[threading.Thread] = None
    result: Any = None
    error: Optional[str] = None


class JobRegistry:
    """Tracks jobs in memory, one active job per provider at a time.

    Enforcing one active job per provider isn't just about UI clarity —
    running two concurrent sessions against a CAPTCHA-sensitive site (Kobo,
    Chirp, Nook) from the same browser profile is a real way to get flagged.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}

    def create(self, provider: str) -> Job:
        with self._lock:
            if self._has_live_job(provider):
                raise JobAlreadyRunningError(
                    f"A job for '{provider}' is already queued or running."
                )
            job = Job(
                id=uuid4().hex,
                provider=provider,
                status="queued",
                created_at=datetime.now(),
                log_queue=queue.Queue(),
                continue_event=threading.Event(),
                cancel_event=threading.Event(),
            )
            self._jobs[job.id] = job
            return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())

    def _has_live_job(self, provider: str) -> bool:
        return any(
            job.provider == provider and job.status in _LIVE_STATUSES
            for job in self._jobs.values()
        )
