from unittest.mock import MagicMock, patch

import pytest

from lib.cancellation import OperationCancelled
from lib.http_retry import _error_reason, request_json


def _response(status_code=200, json_data=None, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.json.return_value = json_data if json_data is not None else {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"{status_code} error")
    else:
        resp.raise_for_status.side_effect = None
    return resp


@patch("lib.http_retry.time.sleep")
@patch("lib.http_retry.requests.get")
def test_request_json_success(mock_get, mock_sleep):
    mock_get.return_value = _response(200, {"ok": True})
    result = request_json("http://example.com", "ctx")
    assert result == {"ok": True}
    mock_sleep.assert_not_called()


@patch("lib.http_retry.time.sleep")
@patch("lib.http_retry.requests.get")
def test_request_json_404_returns_none(mock_get, mock_sleep):
    mock_get.return_value = _response(404)
    result = request_json("http://example.com", "ctx")
    assert result is None
    mock_sleep.assert_not_called()


@patch("lib.http_retry.time.sleep")
@patch("lib.http_retry.requests.get")
def test_request_json_exhausts_retries_returns_none(mock_get, mock_sleep):
    mock_get.return_value = _response(500)
    result = request_json("http://example.com", "ctx", max_retries=2)
    assert result is None
    assert mock_get.call_count == 2


@patch("lib.http_retry.time.sleep")
@patch("lib.http_retry.requests.get")
def test_request_json_429_honors_retry_after_header(mock_get, mock_sleep):
    mock_get.side_effect = [
        _response(429, headers={"Retry-After": "3"}),
        _response(200, {"ok": True}),
    ]
    result = request_json("http://example.com", "ctx")
    assert result == {"ok": True}
    mock_sleep.assert_called_once_with(3.0)


@patch("lib.http_retry.time.sleep")
@patch("lib.http_retry.requests.get")
def test_request_json_429_without_retry_after_uses_minimum_wait(mock_get, mock_sleep):
    mock_get.side_effect = [
        _response(429),
        _response(200, {"ok": True}),
    ]
    result = request_json("http://example.com", "ctx", base_backoff=2.0)
    assert result == {"ok": True}
    # base_backoff (2.0) is less than the 5s floor for a 429 wait; a little
    # jitter (0-3s) is added on top, so allow for that range rather than an
    # exact value.
    mock_sleep.assert_called_once()
    waited = mock_sleep.call_args[0][0]
    assert 5.0 <= waited <= 8.0


@patch("lib.http_retry.time.sleep")
@patch("lib.http_retry.requests.get")
def test_request_json_cancel_fn_stops_before_first_attempt(mock_get, mock_sleep):
    with pytest.raises(OperationCancelled):
        request_json("http://example.com", "ctx", cancel_fn=lambda: True)
    mock_get.assert_not_called()


@patch("lib.http_retry.time.sleep")
@patch("lib.http_retry.requests.get")
def test_request_json_429_fires_on_rate_limited_before_giving_up(mock_get, mock_sleep):
    mock_get.return_value = _response(429)
    on_rate_limited = MagicMock()

    result = request_json(
        "http://example.com", "ctx", max_retries=1, on_rate_limited=on_rate_limited
    )

    assert result is None
    on_rate_limited.assert_called_once()


@patch("lib.http_retry.time.sleep")
@patch("lib.http_retry.requests.get")
def test_request_json_fires_on_exhausted_after_all_retries_fail(mock_get, mock_sleep):
    mock_get.return_value = _response(503)
    on_exhausted = MagicMock()

    result = request_json(
        "http://example.com", "ctx", max_retries=2, on_exhausted=on_exhausted
    )

    assert result is None
    on_exhausted.assert_called_once()


@patch("lib.http_retry.time.sleep")
@patch("lib.http_retry.requests.get")
def test_request_json_404_does_not_fire_on_exhausted(mock_get, mock_sleep):
    mock_get.return_value = _response(404)
    on_exhausted = MagicMock()

    result = request_json("http://example.com", "ctx", on_exhausted=on_exhausted)

    assert result is None
    on_exhausted.assert_not_called()


@patch("lib.http_retry.time.sleep")
@patch("lib.http_retry.requests.get")
def test_request_json_success_does_not_fire_on_exhausted(mock_get, mock_sleep):
    mock_get.return_value = _response(200, {"ok": True})
    on_exhausted = MagicMock()

    result = request_json("http://example.com", "ctx", on_exhausted=on_exhausted)

    assert result == {"ok": True}
    on_exhausted.assert_not_called()


def test_error_reason_extracts_google_style_error_body():
    resp = _response(
        429,
        json_data={
            "error": {
                "message": "Quota exceeded for quota metric 'Queries'.",
                "errors": [{"reason": "rateLimitExceeded"}],
            }
        },
    )
    reason = _error_reason(resp)
    assert "rateLimitExceeded" in reason
    assert "Quota exceeded" in reason


def test_error_reason_returns_empty_string_when_no_error_body():
    resp = _response(429, json_data={})
    assert _error_reason(resp) == ""


@patch("lib.http_retry.requests.get")
def test_request_json_cancel_fn_interrupts_a_429_wait(mock_get):
    mock_get.return_value = _response(429, headers={"Retry-After": "30"})

    calls = {"n": 0}

    def cancel_fn():
        calls["n"] += 1
        # Let the first pre-attempt check pass, then cancel during the sleep.
        return calls["n"] > 2

    with patch("lib.http_retry.time.sleep") as mock_sleep:
        with pytest.raises(OperationCancelled):
            request_json("http://example.com", "ctx", cancel_fn=cancel_fn)

    # Slept in ~1s slices rather than the full 30s in one call.
    assert mock_sleep.call_count >= 1
    assert all(call.args[0] <= 1.0 for call in mock_sleep.call_args_list)
