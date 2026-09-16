"""OpenGrants grant search client (API key required) — async httpx I/O.

OpenGrants (https://opengrants.io) covers federal, state and private grants. One
GET to /functions/v1/grants-api returns grants with description, award range,
deadline, categories, funder and a `geography` field ("United States", a state,
…), ranked by hybrid keyword + semantic relevance. Rows are normalized to the
same shape as the other sources, and `geography` is passed on as `state`, so the
strict location gate can tell a California grant from a nationwide one.

The API is metered (X-RateLimit-Limit per day), so a search makes one call (a second
only when the user's state has no matches): no retries, identical searches reuse
recent results, and once the daily quota is used up the source is skipped until
the quota resets at midnight UTC.

Set OPENGRANTS_API_KEY in .env. Without a key this source returns no rows.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from services import source_health
from services.async_utils import build_async_client, json_body, run_sync
from services.grants_gov import _clean_text, _format_money

logger = logging.getLogger(__name__)

BASE_URL = "https://qnoicxojartltrownmal.supabase.co/functions/v1"
GRANTS_URL = f"{BASE_URL}/grants-api"
CONNECT_TIMEOUT_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 20
SEARCH_MAX_CHARS = 200

# Results per search (last good copy). Reused for identical searches for a short
# while to save metered calls, and as a fallback when a call fails.
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_FALLBACK_MAX_AGE_SECONDS = 6 * 3600
_QUOTA = {"exhausted_until": 0.0}
_WARNED_NO_KEY = {"done": False}

_NATIONAL_RE = re.compile(
    r"\b(?:national|nationwide|united\s+states|u\.s\.a?\.?|usa|all\s+(?:50\s+)?states)\b", re.I
)
_TAG_RE = re.compile(r"<[^>]+>")


def _api_key() -> str:
    return (os.getenv("OPENGRANTS_API_KEY") or "").strip()


def _env_number(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(low, min(high, value))


def _reuse_seconds() -> float:
    """Identical searches reuse results this long (default 30 min) to save metered calls."""
    return _env_number("OPENGRANTS_CACHE_SECONDS", 1800, 0, 86400)


def _search_budget_seconds() -> float:
    return _env_number("OPENGRANTS_TIMEOUT_SECONDS", 15, 1, 120)


def _page_size() -> int:
    """One call returns up to this many grants (the API allows 1–100)."""
    return int(_env_number("OPENGRANTS_RESULTS", 25, 1, 100))


def _plain_text(value: Any, limit: int = 0) -> str:
    text = _TAG_RE.sub(" ", html.unescape(str(value or "")))
    return _clean_text(re.sub(r"\s+", " ", text), limit)


def _geography(value: Any) -> str:
    """
    OpenGrants `geography` → the `state` field the location gate reads.
    "United States" / "National" becomes "Nationwide"; text naming states is kept.
    """
    from services.eligibility import _state_names_in

    text = _clean_text(value)
    if not text:
        return ""
    if _NATIONAL_RE.search(text) and not _state_names_in(text):
        return "Nationwide"
    return text


def _normalize_grant(item: dict[str, Any]) -> dict[str, Any]:
    """One OpenGrants grant → the shared grant row shape."""
    floor = _format_money(item.get("amount_min"))
    ceiling = _format_money(item.get("amount_max"))
    amount = ceiling or floor
    if floor and ceiling and floor != ceiling:
        amount = f"{floor} – {ceiling}"

    relevance = item.get("relevance")
    try:
        fit_score: Any = max(0.0, min(1.0, float(relevance))) if relevance not in (None, "") else ""
    except (TypeError, ValueError):
        fit_score = ""

    categories = item.get("categories") if isinstance(item.get("categories"), list) else []
    return {
        "source": "opengrants",
        "title": _plain_text(item.get("title")),
        "agency": _clean_text(item.get("funder_name")),
        "agency_code": "",
        "agency_address": "",
        "agency_contact": "",
        "agency_email": "",
        "agency_phone": "",
        "top_agency": "",
        "deadline": _clean_text(item.get("deadline_date")),
        # `created_at` is when OpenGrants indexed the grant, not when it was
        # posted, so it must not feed the "posted too long ago" freshness check.
        "open_date": "",
        "eligibility": "",
        "url": _clean_text(item.get("listing_url")),
        "description": _plain_text(item.get("description"), 1200),
        "opp_status": _clean_text(item.get("status")) or "open",
        "number": "",
        "id": _clean_text(item.get("id")),
        "amount": amount,
        "award_ceiling": ceiling,
        "award_floor": floor,
        "doc_type": "opengrants",
        "alns": "",
        "funding_categories": ", ".join(_clean_text(c) for c in categories if _clean_text(c)),
        "funding_instruments": "",
        "cost_sharing": "",
        "number_of_awards": "",
        "state": _geography(item.get("geography")),
        "fit_score": fit_score,
        "match_reasons": "",
    }


def _search_params(
    keyword: str, priority_area: str, location_state: str, rows: int, *, include_national: bool = False
) -> dict[str, str]:
    """
    One request: the user's topic (hybrid keyword + semantic ranking), open
    opportunities only. With a state, the call asks for that state's grants: when
    nationwide programs are included they crowd out state grants in the ranking,
    and those federal programs already come from Grants.gov / Simpler.Grants.gov.
    Past deadlines, budget and eligibility are screened afterwards by the shared
    pipeline, so grants with a rolling (empty) deadline are not lost to a
    server-side date filter.
    """
    search = (keyword or priority_area or "").strip()[:SEARCH_MAX_CHARS]
    params = {"opportunity_type": "open", "limit": str(max(1, min(int(rows), 100)))}
    if search:
        params["search"] = search
        params["search_mode"] = "hybrid"
    state = (location_state or "").strip().upper()
    if len(state) == 2 and state.isalpha():
        params["states"] = state
        params["include_national"] = "true" if include_national else "false"
    return params


def _next_utc_midnight(now: float) -> float:
    today = datetime.fromtimestamp(now, tz=timezone.utc).date()
    return datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc).timestamp()


def _note_quota(response: httpx.Response) -> None:
    """Stop calling once the daily quota is used up (resets at midnight UTC)."""
    if response.status_code == 429:
        _QUOTA["exhausted_until"] = _next_utc_midnight(time.time())
        return
    try:
        remaining = int(response.headers.get("x-ratelimit-remaining", ""))
    except ValueError:
        return
    if remaining == 0:
        _QUOTA["exhausted_until"] = _next_utc_midnight(time.time())


async def _get_grants_async(
    client: httpx.AsyncClient, params: dict[str, str]
) -> list[dict[str, Any]] | None:
    """One metered call. Returns rows, [] for no matches, or None on failure (never retried)."""
    try:
        response = await client.get(GRANTS_URL, params=params)
    except httpx.TimeoutException:
        logger.warning("OpenGrants search timed out")
        source_health.report("opengrants", source_health.TIMEOUT)
        return None
    except httpx.HTTPError as exc:
        logger.warning("OpenGrants request failed: %s", exc)
        source_health.report("opengrants", source_health.UNAVAILABLE)
        return None

    _note_quota(response)
    status = response.status_code
    if status == 200:
        body = json_body(response)
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list):
            logger.warning("OpenGrants returned an unexpected response body")
            source_health.report("opengrants", source_health.UNAVAILABLE)
            return None
        source_health.clear("opengrants")
        return [row for row in results if isinstance(row, dict)]
    source_health.report("opengrants", source_health.reason_for_status(status))
    if status == 401:
        logger.error("OpenGrants rejected the API key (HTTP 401); check OPENGRANTS_API_KEY")
    elif status == 403:
        logger.error("OpenGrants plan does not include API access (HTTP 403)")
    elif status == 429:
        logger.warning("OpenGrants daily request quota used up; skipping it until midnight UTC")
    else:
        logger.warning("OpenGrants search failed: HTTP %s %s", status, response.text[:300])
    return None


def _client() -> httpx.AsyncClient:
    return build_async_client(
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        read_timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"Authorization": f"Bearer {_api_key()}"},
        max_connections=2,
    )


async def search_opportunities_async(
    *,
    keyword: str = "",
    priority_area: str = "",
    location_city: str = "",
    location_state: str = "",
    rows: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """
    Search OpenGrants for open grants (async). Never raises; returns [] when the
    key is missing, the quota is used up with no recent results, or the API fails.
    """
    if not _api_key():
        if not _WARNED_NO_KEY["done"]:
            logger.warning("OPENGRANTS_API_KEY is not set; skipping OpenGrants")
            _WARNED_NO_KEY["done"] = True
        source_health.not_configured("opengrants")
        return []

    from services.location_utils import normalize_location

    _, state = normalize_location(location_city, location_state)
    params = _search_params(keyword, priority_area, state, rows or _page_size())
    cache_key = json.dumps(params, sort_keys=True)
    now = time.time()
    cached = _CACHE.get(cache_key)
    reuse_for = _reuse_seconds()
    if reuse_for and cached and now - cached[0] < reuse_for:
        return list(cached[1])

    def _fallback(why: str) -> list[dict[str, Any]]:
        if cached and now - cached[0] < _FALLBACK_MAX_AGE_SECONDS:
            logger.warning("OpenGrants %s; using results from %.0f min ago", why, (now - cached[0]) / 60)
            source_health.clear("opengrants")
            return list(cached[1])
        return []

    if _QUOTA["exhausted_until"] > now:
        source_health.report("opengrants", source_health.QUOTA)
        return _fallback("daily quota is used up")

    async def _run(active: httpx.AsyncClient) -> list[dict[str, Any]] | None:
        found = await _get_grants_async(active, params)
        if found == [] and params.get("include_national") == "false" and _QUOTA["exhausted_until"] <= time.time():
            # Nothing for this state: one more call for nationwide programs.
            wider = _search_params(keyword, priority_area, state, rows or _page_size(), include_national=True)
            found = await _get_grants_async(active, wider)
        if found is None:
            return None
        return [_normalize_grant(item) for item in found]

    async def _within_budget(active: httpx.AsyncClient) -> list[dict[str, Any]] | None:
        budget = _search_budget_seconds()
        try:
            return await asyncio.wait_for(_run(active), timeout=budget)
        except asyncio.TimeoutError:
            logger.warning("OpenGrants did not answer within %.0fs; continuing without it", budget)
            source_health.report("opengrants", source_health.TIMEOUT)
        except Exception:
            logger.warning("OpenGrants search failed", exc_info=True)
            source_health.report("opengrants", source_health.UNAVAILABLE)
        return None

    if client is not None:
        results = await _within_budget(client)
    else:
        async with _client() as owned:
            results = await _within_budget(owned)

    if results is None:
        return _fallback("is unavailable")
    _CACHE[cache_key] = (time.time(), results)
    return list(results)


def search_opportunities(
    *,
    keyword: str = "",
    priority_area: str = "",
    location_city: str = "",
    location_state: str = "",
    rows: int | None = None,
) -> list[dict[str, Any]]:
    """Sync bridge for search_opportunities_async()."""
    return run_sync(
        search_opportunities_async(
            keyword=keyword,
            priority_area=priority_area,
            location_city=location_city,
            location_state=location_state,
            rows=rows,
        )
    )
