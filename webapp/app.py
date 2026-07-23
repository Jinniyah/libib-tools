# webapp/app.py
#
# FastAPI app factory: dashboard, job control (events/continue/cancel),
# scraper dispatch, and downloads.

from __future__ import annotations

import importlib
import inspect
import os
import queue as queue_module
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, is_dataclass
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from webapp.jobs.log_bridge import install as install_log_bridge
from webapp.jobs.registry import Job, JobAlreadyRunningError, JobRegistry
from webapp.jobs.runner import start_job

# Loads a `.env` file (KINDLE_EMAIL/PASSWORD, AI_PROVIDER, OPENAI_API_KEY) from
# the current or a parent directory, if one exists — a silent no-op otherwise.
# Never overrides a real environment variable already set (load_dotenv's
# default). Module-level so it also covers `uvicorn webapp.app:app` directly,
# not just `python -m webapp`.
load_dotenv()

# Delay before the process actually exits after /shutdown responds, so the
# HTTP response reaches the browser first.
_SHUTDOWN_DELAY_SECONDS = 0.3

_BASE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _BASE_DIR / "templates"
_STATIC_DIR = _BASE_DIR / "static"

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_QUEUE_POLL_TIMEOUT = 0.5

# Providers with a working scraper run() entry point — same convention as
# libib_reconcile/core.py's _SCRAPER_MODULES, duplicated rather than shared
# since the two packages are deliberately independent of each other.
_SCRAPER_MODULES: dict[str, str] = {
    "chirp": "chirp_to_libib.core",
    "kindle": "kindle_to_libib.core",
    "kobo": "kobo_to_libib.core",
    "nook": "nook_to_libib.core",
}

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Process-wide singletons: one job registry, one log bridge installation.
registry = JobRegistry()
install_log_bridge()


class ScrapeOptions(BaseModel):
    pages: Optional[int] = None
    dry_run: bool = False
    output_dir: str = "."
    no_enrich: bool = False


@dataclass
class ToolCard:
    slug: str
    name: str
    description: str
    enabled: bool
    tag: str


# Static for now — becomes data-driven (last run, book count) once the job
# registry (GUI-Backend-2) and its status tracking exist.
TOOL_CARDS: list[ToolCard] = [
    ToolCard(
        slug="chirp",
        name="Chirp",
        description=(
            "Scrapes your Chirp Books audiobook library. Login is manual — a "
            "browser window opens and pauses for you to sign in, since Chirp's "
            "bot-detection blocks automated login."
        ),
        enabled=True,
        tag="chirp,audiobook",
    ),
    ToolCard(
        slug="kindle",
        name="Kindle",
        description=(
            "Scrapes your Amazon Kindle ebook library. Login is automated via "
            "environment variables (or a one-time prompt) — no manual browser "
            "step needed."
        ),
        enabled=True,
        tag="kindle,ebook",
    ),
    ToolCard(
        slug="kobo",
        name="Kobo",
        description=(
            "Scrapes your Rakuten Kobo ebook library. Login is manual, using a "
            "two-tab workaround to get past Kobo's hCaptcha."
        ),
        enabled=True,
        tag="kobo,ebook",
    ),
    ToolCard(
        slug="nook",
        name="Nook",
        description=(
            "Scrapes your Barnes & Noble Nook ebook library. Login is manual — "
            "B&N's Akamai bot-detection blocks automated login, same as Chirp."
        ),
        enabled=True,
        tag="nook,ebook",
    ),
    ToolCard(
        slug="google",
        name="Google Books",
        description=(
            "Coming soon. Planned to use the Google Books API directly (OAuth "
            "2.0 consent flow) instead of Selenium — no browser scraping "
            "involved once it ships."
        ),
        enabled=False,
        tag="google,ebook",
    ),
]


def _build_run_callable(module: Any, options: ScrapeOptions) -> Any:
    """Build the run_callable start_job() expects: a function of (wait_fn,
    cancel_fn) that calls the scraper's own run(). Kindle's run() has no
    wait_fn parameter (automated login) — inspecting the signature, rather
    than hardcoding per-provider, keeps this generic across all four
    scrapers (and any future one that likewise omits wait_fn/cancel_fn).

    Kindle's run() also accepts credentials_fn — swapped to the module's own
    credentials_from_env() when present, instead of the CLI default
    (_prompt_credentials) which blocks on stdin. A web page has no terminal to
    answer an input() prompt, so the CLI default would hang the whole server
    with no way to cancel; credentials_from_env() raises immediately instead,
    with a copy-pasteable .env snippet in the error.
    """
    run_params = inspect.signature(module.run).parameters
    accepts_wait_fn = "wait_fn" in run_params
    accepts_cancel_fn = "cancel_fn" in run_params
    credentials_from_env = getattr(module, "credentials_from_env", None)
    accepts_credentials_fn = (
        "credentials_fn" in run_params and credentials_from_env is not None
    )

    def run_callable(wait_fn: Any, cancel_fn: Any) -> Any:
        kwargs: dict[str, Any] = {
            "pages": options.pages,
            "dry_run": options.dry_run,
            "output_dir": options.output_dir,
            "no_enrich": options.no_enrich,
        }
        if accepts_wait_fn:
            kwargs["wait_fn"] = wait_fn
        if accepts_cancel_fn:
            kwargs["cancel_fn"] = cancel_fn
        if accepts_credentials_fn:
            kwargs["credentials_fn"] = credentials_from_env
        return module.run(**kwargs)

    return run_callable


def _extract_result_paths(result: Any) -> list[str]:
    """Pull every string field off a RunResult/ReconcileRunResult-shaped
    dataclass — generic across both, since both are just a handful of
    Optional[str] path fields plus some int counts."""
    if result is None or not is_dataclass(result):
        return []
    return [v for v in vars(result).values() if isinstance(v, str)]


def _latest_status_per_provider() -> dict[str, str]:
    """Most recent job's status per provider, for the dashboard's status
    badges. A provider with no job yet simply has no entry.

    Uses `>=`, not `>`: registry.all() returns jobs in creation (insertion)
    order, and datetime.now() resolution isn't always fine enough to
    distinguish two jobs created in rapid succession — `>=` lets the
    later-inserted job win on a timestamp tie, which is the actually-correct
    "most recent" job either way.
    """
    latest: dict[str, Job] = {}
    for job in registry.all():
        current = latest.get(job.provider)
        if current is None or job.created_at >= current.created_at:
            latest[job.provider] = job
    return {provider: job.status for provider, job in latest.items()}


def _get_job_or_404(provider: str, job_id: str) -> Job:
    job = registry.get(job_id)
    if job is None or job.provider != provider:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _any_job_live() -> bool:
    return any(job.status not in _TERMINAL_STATUSES for job in registry.all())


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # Shutdown (Ctrl-C, or the process exiting normally): cancel any jobs
    # still in flight, same cooperative cancel_event the /cancel route and
    # /shutdown route use — see webapp/jobs/runner.py and each scraper
    # core's cancel_fn checks between pages/books/retries.
    for job in registry.all():
        if job.status not in _TERMINAL_STATUSES:
            job.cancel_event.set()


def _job_event_stream(job: Job) -> Iterator[str]:
    """SSE generator: emits a named `event: status` whenever job.status
    changes (so the page can show/hide the login-wait Continue button and
    update the status badge without polling), drains job.log_queue as
    unnamed `data:` lines arrive, then emits a final `event: done` once the
    job has reached a terminal status and the queue is empty — not before,
    so buffered log lines are never dropped.
    """
    last_status: Optional[str] = None
    while True:
        status = job.status
        if status != last_status:
            yield f"event: status\ndata: {status}\n\n"
            last_status = status

        try:
            line = job.log_queue.get(timeout=_QUEUE_POLL_TIMEOUT)
            yield f"data: {line}\n\n"
        except queue_module.Empty:
            pass

        if status in _TERMINAL_STATUSES and job.log_queue.empty():
            yield f"event: done\ndata: {status}\n\n"
            break


def create_app() -> FastAPI:
    app = FastAPI(title="LibibTools", lifespan=_lifespan)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/")
    def dashboard(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {"tools": TOOL_CARDS, "statuses": _latest_status_per_provider()},
        )

    @app.get("/api/jobs-live")
    def jobs_live() -> dict[str, bool]:
        """Backs the Quit button's confirm dialog — whether any job is
        currently queued/waiting/running, so it only warns when true."""
        return {"jobs_running": _any_job_live()}

    @app.post("/shutdown")
    def shutdown() -> dict[str, Any]:
        """Cancel any in-flight jobs, then force-exit the process shortly
        after this response is sent (long enough for the browser to receive
        it, not long enough to matter). A job actively mid-scrape (a live
        Selenium call in progress) may not unwind cleanly — same known
        limitation as /cancel; the client warns first if jobs_running."""
        jobs_running = _any_job_live()
        for job in registry.all():
            if job.status not in _TERMINAL_STATUSES:
                job.cancel_event.set()

        threading.Timer(_SHUTDOWN_DELAY_SECONDS, os._exit, args=(0,)).start()
        return {"ok": True, "jobs_running": jobs_running}

    @app.get("/scrape/{provider}")
    def scrape_page(request: Request, provider: str) -> HTMLResponse:
        tool = next((t for t in TOOL_CARDS if t.slug == provider), None)
        if tool is None or not tool.enabled:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown or not-yet-available provider '{provider}'.",
            )
        return templates.TemplateResponse(request, "scrape.html", {"tool": tool})

    @app.get("/scrape/{provider}/jobs/{job_id}/events")
    def scrape_job_events(provider: str, job_id: str) -> StreamingResponse:
        job = _get_job_or_404(provider, job_id)
        return StreamingResponse(_job_event_stream(job), media_type="text/event-stream")

    @app.get("/scrape/{provider}/jobs/{job_id}")
    def scrape_job_detail(provider: str, job_id: str) -> dict[str, Any]:
        job = _get_job_or_404(provider, job_id)
        return {
            "job_id": job.id,
            "status": job.status,
            "error": job.error,
            "output_dir": job.output_dir,
            "downloads": [
                {"filename": Path(p).name, "url": f"/downloads/{job.id}/{Path(p).name}"}
                for p in _extract_result_paths(job.result)
            ],
        }

    @app.post("/scrape/{provider}/jobs/{job_id}/continue")
    def scrape_job_continue(provider: str, job_id: str) -> dict[str, bool]:
        job = _get_job_or_404(provider, job_id)
        if job.status != "waiting_for_login":
            raise HTTPException(
                status_code=409,
                detail=f"Job is not waiting for login (status: {job.status}).",
            )
        job.continue_event.set()
        return {"ok": True}

    @app.post("/scrape/{provider}/jobs/{job_id}/cancel")
    def scrape_job_cancel(provider: str, job_id: str) -> dict[str, bool]:
        job = _get_job_or_404(provider, job_id)
        if job.status in _TERMINAL_STATUSES:
            raise HTTPException(
                status_code=409, detail=f"Job already finished (status: {job.status})."
            )
        # Observed at the login-wait poll and between scrape pages/books/
        # retries (see each scraper core's cancel_fn checks) — cooperative,
        # so it doesn't interrupt a single Selenium call already in flight.
        job.cancel_event.set()
        return {"ok": True}

    @app.post("/scrape/{provider}/jobs")
    def scrape_job_start(provider: str, options: ScrapeOptions) -> dict[str, str]:
        module_name = _SCRAPER_MODULES.get(provider)
        if module_name is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown or not-yet-built provider '{provider}'.",
            )

        module = importlib.import_module(module_name)
        run_callable = _build_run_callable(module, options)

        try:
            job = start_job(registry, provider, run_callable)
        except JobAlreadyRunningError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        # Resolved to an absolute path so the GUI can tell the user exactly
        # where files landed — skipped for dry runs, which write nothing.
        if not options.dry_run:
            job.output_dir = str(Path(options.output_dir).resolve())

        return {"job_id": job.id, "status": job.status}

    @app.get("/downloads/{job_id}/{filename}")
    def download_file(job_id: str, filename: str) -> FileResponse:
        job = registry.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")

        # Never join `filename` onto a directory — only ever serve a path
        # that's already one of this job's own known result paths (produced
        # by our own code, never by user input), matched by basename.
        for path in _extract_result_paths(job.result):
            candidate = Path(path)
            if candidate.name == filename and candidate.is_file():
                return FileResponse(candidate, filename=filename)

        raise HTTPException(status_code=404, detail="File not found for this job")

    return app


app = create_app()
