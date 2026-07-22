# Note: these tests always mock webapp.app.importlib.import_module and
# dispatch against the *real* provider slugs already in _SCRAPER_MODULES
# (chirp/kindle/...) rather than patching _SCRAPER_MODULES to a fake mapping.
# Combining a patch on _SCRAPER_MODULES with a patch on importlib.import_module
# in the same test breaks the _SCRAPER_MODULES patch (confirmed empirically —
# some interaction in how mock.patch resolves nested targets under the same
# module path). Since import_module itself is mocked, it returns our fake
# module regardless of which real module name string gets looked up, so there
# was never a need to fake the mapping too.

import os
import tempfile
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional
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


def _fake_run_with_wait_fn(
    *, pages=None, dry_run=False, output_dir=".", no_enrich=False, wait_fn=None
):
    return SimpleNamespace(
        received_wait_fn=wait_fn is not None,
        csv_path="/tmp/fake_chirp.csv",
        unresolved_path=None,
        total_books=1,
        resolved_count=1,
    )


def _fake_run_without_wait_fn(
    *, pages=None, dry_run=False, output_dir=".", no_enrich=False
):
    return SimpleNamespace(
        received_wait_fn=False,
        csv_path="/tmp/fake_kindle.csv",
        unresolved_path=None,
        total_books=2,
        resolved_count=2,
    )


# ==========================
# POST /scrape/{provider}/jobs
# ==========================


def test_start_job_unknown_provider_returns_404():
    response = client.post("/scrape/google/jobs", json={})
    assert response.status_code == 404


@patch("webapp.app.importlib.import_module")
def test_start_job_wait_fn_provider_receives_wait_fn(mock_import_module):
    # Real provider slug ("chirp"), fake module behind it — import_module is
    # mocked, so it returns our fake regardless of which real module name
    # string _SCRAPER_MODULES looks up. (Patching _SCRAPER_MODULES itself
    # doesn't combine cleanly with patching importlib.import_module in the
    # same test — see the module docstring-equivalent note in this file.)
    mock_import_module.return_value = SimpleNamespace(run=_fake_run_with_wait_fn)

    response = client.post("/scrape/chirp/jobs", json={"output_dir": "/tmp"})
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    job = registry.get(job_id)
    assert _wait_for(lambda: job.status == "completed")
    assert job.result.received_wait_fn is True
    assert job.result.csv_path == "/tmp/fake_chirp.csv"
    mock_import_module.assert_any_call("chirp_to_libib.core")


@patch("webapp.app.importlib.import_module")
def test_start_job_no_wait_fn_provider_does_not_receive_one(mock_import_module):
    mock_import_module.return_value = SimpleNamespace(run=_fake_run_without_wait_fn)

    response = client.post("/scrape/kindle/jobs", json={})
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    job = registry.get(job_id)
    assert _wait_for(lambda: job.status == "completed")
    assert job.result.received_wait_fn is False


@patch("webapp.app.importlib.import_module")
def test_start_job_conflict_when_already_running(mock_import_module):
    def slow_run(
        *, pages=None, dry_run=False, output_dir=".", no_enrich=False, wait_fn=None
    ):
        if wait_fn is not None:
            wait_fn()
        return SimpleNamespace(
            csv_path=None, unresolved_path=None, total_books=0, resolved_count=0
        )

    mock_import_module.return_value = SimpleNamespace(run=slow_run)

    first = client.post("/scrape/chirp/jobs", json={})
    assert first.status_code == 200
    job = registry.get(first.json()["job_id"])
    assert _wait_for(lambda: job.status == "waiting_for_login")

    second = client.post("/scrape/chirp/jobs", json={})
    assert second.status_code == 409

    job.cancel_event.set()
    job.thread.join(timeout=2)


# ==========================
# GET /downloads/{job_id}/{filename}
# ==========================


@dataclass
class _FakeResult:
    csv_path: Optional[str]
    unresolved_path: Optional[str] = None


def test_download_unknown_job_returns_404():
    response = client.get("/downloads/does-not-exist/whatever.csv")
    assert response.status_code == 404


def test_download_unknown_filename_returns_404():
    job = registry.create("download-unknown-filename")
    job.result = _FakeResult(csv_path="/nonexistent/path/real.csv")

    response = client.get(f"/downloads/{job.id}/not-the-real-file.csv")
    assert response.status_code == 404


def test_download_path_traversal_filename_rejected():
    job = registry.create("download-traversal")
    job.result = _FakeResult(csv_path="/nonexistent/path/real.csv")

    response = client.get(f"/downloads/{job.id}/..%2F..%2F..%2Fetc%2Fpasswd")
    assert response.status_code == 404


def test_download_serves_real_result_file():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "gap.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("title,creators\nDune,Frank Herbert\n")

        job = registry.create("download-real-file")
        job.result = _FakeResult(csv_path=path)

        response = client.get(f"/downloads/{job.id}/gap.csv")
        assert response.status_code == 200
        assert "Dune" in response.text


def test_download_no_result_yet_returns_404():
    job = registry.create("download-no-result")
    response = client.get(f"/downloads/{job.id}/anything.csv")
    assert response.status_code == 404
