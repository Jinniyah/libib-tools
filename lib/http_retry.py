# lib/http_retry.py — shared GET-with-retry helper for the enrichment pipeline.
#
# Previously duplicated (and already drifted — 3 vs 4 max_retries) between
# lib/enricher.py's _http_get_json and lib/openlibrary.py's _ol_query. Centralized
# here so the 429/Retry-After handling and cooperative-cancel support only need
# to be right in one place.

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import Optional
from urllib.parse import urlparse

import requests

from lib.cancellation import OperationCancelled

log = logging.getLogger(__name__)

# Google Books' actual rate-limit window is longer than the 2/4/8s exponential
# backoff used for ordinary errors — observed 3 consecutive 429s at that spacing.
_MIN_429_WAIT = 5.0
# Jitter added on top of the 429 wait so consecutive retries aren't spaced at
# the exact same mechanical interval every time.
_429_JITTER_RANGE = (0.0, 3.0)


def request_json(
    url: str,
    context: str,
    *,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    max_retries: int = 3,
    base_backoff: float = 2.0,
    cancel_fn: Optional[Callable[[], bool]] = None,
    on_rate_limited: Optional[Callable[[], None]] = None,
) -> Optional[dict]:
    """GET a URL and return parsed JSON, with retry/backoff. None on 404 or exhaustion.

    A 429 is handled distinctly from other failures: honors the response's
    Retry-After header when present, otherwise waits at least _MIN_429_WAIT
    seconds. If cancel_fn is given, it's checked before each attempt and in
    ~1s slices during any wait, so a cancel lands quickly even mid-backoff.
    on_rate_limited, if given, fires the moment a 429 is seen (before the
    wait) — lets a caller trip its own circuit breaker instead of learning
    about the rate limit only after every retry is exhausted.
    """
    backoff = base_backoff

    for attempt in range(1, max_retries + 1):
        if cancel_fn is not None and cancel_fn():
            raise OperationCancelled()

        host = urlparse(url).netloc

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=20)
        except Exception as exc:
            log.warning(
                "HTTP error fetching '%s' from %s (attempt %d/%d): %s",
                context,
                host,
                attempt,
                max_retries,
                exc,
            )
            _sleep(backoff, cancel_fn)
            backoff *= 2
            continue

        if resp.status_code == 404:
            return None

        if resp.status_code == 429:
            if on_rate_limited is not None:
                on_rate_limited()
            retry_after = _retry_after_seconds(resp)
            wait = retry_after or (
                max(backoff, _MIN_429_WAIT) + random.uniform(*_429_JITTER_RANGE)
            )
            log.warning(
                "Rate limited fetching '%s' from %s (attempt %d/%d) — waiting %.1fs%s. %s",
                context,
                host,
                attempt,
                max_retries,
                wait,
                "" if retry_after else " (no Retry-After header — guessing)",
                _error_reason(resp),
            )
            _sleep(wait, cancel_fn)
            backoff *= 2
            continue

        try:
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.warning(
                "HTTP error fetching '%s' from %s (attempt %d/%d): %s",
                context,
                host,
                attempt,
                max_retries,
                exc,
            )
            _sleep(backoff, cancel_fn)
            backoff *= 2

    return None


def _error_reason(resp: "requests.Response") -> str:
    """Pull whatever detail a JSON error body offers (Google's APIs typically
    include an error.errors[].reason like 'rateLimitExceeded' vs
    'dailyLimitExceeded' — the difference between a short burst limit and one
    that won't clear until a day boundary) so it ends up in the log instead
    of being silently discarded."""
    try:
        body = resp.json()
    except ValueError:
        return ""

    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return ""

    message = error.get("message", "")
    sub_errors = error.get("errors") or []
    reason = sub_errors[0].get("reason", "") if sub_errors else ""
    detail = ", ".join(part for part in (reason, message) if part)
    return f"Response detail: {detail}" if detail else ""


def _retry_after_seconds(resp: "requests.Response") -> Optional[float]:
    value = resp.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None  # HTTP-date form — not worth parsing for this use case


def _sleep(seconds: float, cancel_fn: Optional[Callable[[], bool]]) -> None:
    """Sleep in ~1s slices so a pending cancel lands quickly instead of after
    the full wait."""
    if cancel_fn is None:
        time.sleep(seconds)
        return

    remaining = seconds
    slice_len = 1.0
    while remaining > 0:
        if cancel_fn():
            raise OperationCancelled()
        time.sleep(min(slice_len, remaining))
        remaining -= slice_len
