# lib/cancellation.py — shared cancellation signal for long-running scrapes.
#
# One exception type shared by the scraper cores (raised when a caller-supplied
# cancel_fn reports True) and the web GUI's job runner (which catches it and
# marks the job "cancelled") — see webapp/jobs/runner.py.

from __future__ import annotations


class OperationCancelled(Exception):
    """Raised to unwind out of a scrape/enrichment run that was asked to stop early."""
