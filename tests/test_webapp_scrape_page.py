from dataclasses import dataclass
from typing import Optional

from fastapi.testclient import TestClient

from webapp.app import app, registry

client = TestClient(app)


# ==========================
# GET /scrape/{provider} — the run-a-scraper page
# ==========================


def test_scrape_page_renders_for_enabled_provider():
    response = client.get("/scrape/chirp")
    assert response.status_code == 200
    assert "Chirp" in response.text
    assert 'data-provider="chirp"' in response.text


def test_scrape_page_404_for_disabled_provider():
    response = client.get("/scrape/google")
    assert response.status_code == 404


def test_scrape_page_404_for_unknown_provider():
    response = client.get("/scrape/not-a-real-provider")
    assert response.status_code == 404


def test_scrape_page_links_to_app_js():
    response = client.get("/scrape/kindle")
    assert "/static/app.js" in response.text


# ==========================
# GET /scrape/{provider}/jobs/{job_id} — job detail (status + downloads)
# ==========================


@dataclass
class _FakeResult:
    csv_path: Optional[str]
    unresolved_path: Optional[str] = None


def test_job_detail_unknown_job_returns_404():
    response = client.get("/scrape/chirp/jobs/does-not-exist")
    assert response.status_code == 404


def test_job_detail_provider_mismatch_returns_404():
    job = registry.create("detail-mismatch")
    response = client.get(f"/scrape/other-provider/jobs/{job.id}")
    assert response.status_code == 404


def test_job_detail_no_result_yet_has_empty_downloads():
    job = registry.create("detail-no-result")
    response = client.get(f"/scrape/detail-no-result/jobs/{job.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job.id
    assert body["status"] == "queued"
    assert body["downloads"] == []


def test_job_detail_lists_downloads_from_result():
    job = registry.create("detail-with-result")
    job.status = "completed"
    job.result = _FakeResult(
        csv_path="/some/dir/gap.csv", unresolved_path="/some/dir/unresolved.txt"
    )

    response = client.get(f"/scrape/detail-with-result/jobs/{job.id}")
    body = response.json()

    filenames = {d["filename"] for d in body["downloads"]}
    assert filenames == {"gap.csv", "unresolved.txt"}
    urls = {d["url"] for d in body["downloads"]}
    assert f"/downloads/{job.id}/gap.csv" in urls


def test_job_detail_reports_error():
    job = registry.create("detail-failed")
    job.status = "failed"
    job.error = "something broke"

    response = client.get(f"/scrape/detail-failed/jobs/{job.id}")
    assert response.json()["error"] == "something broke"


# ==========================
# SSE — status events (extends test_webapp_events.py)
# ==========================


def test_events_stream_emits_status_event_on_change():
    job = registry.create("events-status-change")
    job.log_queue.put("line 1")
    job.status = "completed"

    with client.stream(
        "GET", f"/scrape/events-status-change/jobs/{job.id}/events"
    ) as response:
        body = "".join(response.iter_text())

    assert "event: status" in body
    assert "data: completed" in body
