from fastapi.testclient import TestClient

from webapp.app import app, registry

client = TestClient(app)


def test_events_unknown_job_returns_404():
    response = client.get("/scrape/chirp/jobs/does-not-exist/events")
    assert response.status_code == 404


def test_events_provider_mismatch_returns_404():
    job = registry.create("events-mismatch-provider")
    with client.stream(
        "GET", f"/scrape/other-provider/jobs/{job.id}/events"
    ) as response:
        assert response.status_code == 404


def test_events_stream_delivers_buffered_lines_then_done():
    job = registry.create("events-buffered")
    job.log_queue.put("line 1")
    job.log_queue.put("line 2")
    job.status = "completed"

    with client.stream(
        "GET", f"/scrape/events-buffered/jobs/{job.id}/events"
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        body = "".join(response.iter_text())

    assert "data: line 1" in body
    assert "data: line 2" in body
    assert "event: done" in body
    assert "data: completed" in body


def test_events_stream_waits_for_terminal_status_before_closing():
    job = registry.create("events-live")
    job.log_queue.put("still running")
    job.status = "running"

    # Flip to completed shortly after the stream starts, from another
    # thread, simulating the job finishing mid-stream.
    import threading
    import time

    def finish():
        time.sleep(0.2)
        job.status = "completed"

    threading.Thread(target=finish, daemon=True).start()

    with client.stream("GET", f"/scrape/events-live/jobs/{job.id}/events") as response:
        body = "".join(response.iter_text())

    assert "data: still running" in body
    assert "event: done" in body
