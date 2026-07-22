from fastapi.testclient import TestClient

from webapp.app import _latest_status_per_provider, app, registry

client = TestClient(app)


def test_dashboard_returns_200():
    response = client.get("/")
    assert response.status_code == 200


def test_dashboard_lists_all_four_live_tools():
    text = client.get("/").text
    for name in ["Chirp", "Kindle", "Kobo", "Nook"]:
        assert name in text


def test_dashboard_shows_disabled_google_placeholder():
    text = client.get("/").text
    assert "Google Books" in text
    assert "Coming soon" in text


def test_dashboard_enabled_tools_have_run_links():
    text = client.get("/").text
    assert 'href="/scrape/chirp"' in text
    assert 'href="/scrape/kindle"' in text


def test_dashboard_google_has_no_run_link():
    text = client.get("/").text
    assert 'href="/scrape/google"' not in text


def test_static_style_css_served():
    response = client.get("/static/style.css")
    assert response.status_code == 200
    assert "css" in response.headers["content-type"]


def test_dashboard_status_badge_reflects_latest_job_for_known_provider():
    # A fresh job for a real dashboard provider ("chirp") is guaranteed to be
    # the most recent by created_at, regardless of what other test modules
    # sharing this in-process registry did earlier or will do later.
    job = registry.create("chirp")  # status: queued
    try:
        text = client.get("/").text
        assert "badge-status-queued" in text
    finally:
        # A "queued" job is a live status — leaving it that way would block
        # other test modules' /scrape/chirp/jobs dispatch calls (registry
        # enforces one active job per provider) since this registry is a
        # shared, process-wide singleton across the whole test session.
        job.status = "completed"


def test_latest_status_per_provider_prefers_the_later_job():
    # Unit-level, not through the rendered page: the shared registry accrues
    # jobs from every test module in the session, so asserting "this string
    # doesn't appear anywhere in the page" is fragile — a *different*
    # provider's card can legitimately show the same status word. Testing
    # _latest_status_per_provider() directly avoids that entirely.
    first = registry.create("kobo")
    first.status = "completed"  # terminal, so a second job for kobo is allowed

    second = registry.create("kobo")
    second.status = "failed"  # also terminal — no cleanup needed

    assert _latest_status_per_provider()["kobo"] == "failed"
