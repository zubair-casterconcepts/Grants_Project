"""
Which grant sources could not answer during one search, and why.

Source clients never raise: a failed call returns no rows, so on its own the
search could not tell "OpenGrants found nothing" from "OpenGrants hit its daily
limit". Clients call `report()` when a call fails and `clear()` when one
succeeds; the search pipeline calls `start()` before running the sources and
reads the result afterwards, so it can say which sources were skipped while the
rest still give results.

Tracking is per search (a ContextVar holding a dict shared with the source
tasks), so concurrent searches by different users never mix. Outside a tracked
search `report()` and `clear()` do nothing.
"""

from __future__ import annotations

from contextvars import ContextVar

QUOTA = "quota"
TIMEOUT = "timeout"
AUTH = "auth"
UNAVAILABLE = "unavailable"
# No API key set: the source is simply not part of this installation, which is
# not a failure to warn about, but it must not be listed as searched either.
NOT_CONFIGURED = "not_configured"

REASON_TEXT = {
    QUOTA: "daily limit reached",
    TIMEOUT: "took too long to respond",
    AUTH: "access key was rejected",
    UNAVAILABLE: "not responding",
}

_OUTCOMES: ContextVar[dict[str, str] | None] = ContextVar("grant_source_outcomes", default=None)


def start() -> dict[str, str]:
    """Begin tracking a search. Create source tasks after this call; read the dict when they finish."""
    outcomes: dict[str, str] = {}
    _OUTCOMES.set(outcomes)
    return outcomes


def report(source: str, reason: str) -> None:
    """A call to `source` failed. The first reason recorded is kept (e.g. quota over a later timeout)."""
    outcomes = _OUTCOMES.get()
    if outcomes is not None:
        outcomes.setdefault(source, reason if reason in REASON_TEXT else UNAVAILABLE)


def not_configured(source: str) -> None:
    """`source` has no API key, so it was not searched."""
    outcomes = _OUTCOMES.get()
    if outcomes is not None:
        outcomes[source] = NOT_CONFIGURED


def failure_reason(outcomes: dict[str, str], source: str) -> str | None:
    """Why `source` failed this search, or None (answered, or not configured)."""
    reason = outcomes.get(source)
    return reason if reason in REASON_TEXT else None


def clear(source: str) -> None:
    """`source` answered (or served usable results), so it did not fail this search."""
    outcomes = _OUTCOMES.get()
    if outcomes is not None:
        outcomes.pop(source, None)


def reason_for_status(status_code: int) -> str:
    if status_code == 429:
        return QUOTA
    if status_code in (401, 403):
        return AUTH
    if status_code == 504:
        return TIMEOUT
    return UNAVAILABLE
