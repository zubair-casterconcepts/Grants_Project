"""GrantedAI.com API client — discover + grants search (async, server-side only)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

from services.async_utils import build_async_client, json_body, run_sync

logger = logging.getLogger(__name__)

BASE_URL = "https://grantedai.com"
DISCOVER_URL = f"{BASE_URL}/api/v1/discover"
GRANTS_URL = f"{BASE_URL}/api/v1/grants"

# Anonymous demo key from Granted docs — override with GRANTED_API_KEY in .env.
_ANON_KEY = "ga_live_c1eee54a9ad8753126b303aeafed3621dd563f67fcc77c74c8b150a028dc42ea"

CONNECT_TIMEOUT = 8
READ_TIMEOUT = 45
MAX_ATTEMPTS = 2

_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_TTL_SECONDS = 300

ORG_TYPE_MAP = {
    "501c3": "Nonprofit",
    "government": "Government",
    "school": "University",
    "other": "",
}


def _api_key() -> str:
    return (os.getenv("GRANTED_API_KEY") or "").strip() or _ANON_KEY


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Accept": "application/json",
        "User-Agent": "GrantsMatcher/1.0",
    }


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _format_amount_range(min_amount: Any, max_amount: Any, display: Any = "") -> str:
    display_text = _clean(display)
    if display_text:
        return display_text

    def _fmt(raw: Any) -> str:
        if raw in (None, ""):
            return ""
        try:
            amount = float(raw)
            return f"${amount:,.0f}"
        except (TypeError, ValueError):
            return _clean(raw)

    low = _fmt(min_amount)
    high = _fmt(max_amount)
    if low and high and low != high:
        return f"{low} – {high}"
    return low or high


def _normalize_grant(row: dict[str, Any], *, from_discover: bool = False) -> dict[str, Any]:
    grant_id = _clean(row.get("id") or row.get("slug"))
    title = _clean(row.get("name") or row.get("title"))
    funder = _clean(row.get("funder") or row.get("agency"))
    summary = _clean(row.get("summary") or row.get("description"))
    eligibility = _clean(row.get("eligibility"))
    deadline = _clean(row.get("deadline"))
    status = _clean(row.get("status") or row.get("source_type") or ("active" if from_discover else ""))
    state = _clean(row.get("state"))
    url = _clean(row.get("rfp_url") or row.get("source_url") or row.get("details_url"))
    details_url = _clean(row.get("details_url"))
    amount = _format_amount_range(
        row.get("amount_min"),
        row.get("amount_max"),
        row.get("amount"),
    )

    reasons = row.get("match_reasons") or []
    if isinstance(reasons, list):
        reason_text = "; ".join(_clean(r) for r in reasons if r)
    else:
        reason_text = _clean(reasons)

    fit_raw = row.get("fit_score")
    fit_score = None
    if fit_raw not in (None, ""):
        try:
            fit_score = float(fit_raw)
            if fit_score > 1:
                fit_score = fit_score / 100.0
            fit_score = max(0.0, min(1.0, fit_score))
        except (TypeError, ValueError):
            fit_score = None

    similarity = row.get("similarity")
    if fit_score is None and similarity not in (None, ""):
        try:
            fit_score = max(0.0, min(1.0, float(similarity)))
        except (TypeError, ValueError):
            fit_score = None

    tags = row.get("tags") or []
    if isinstance(tags, list):
        tags_text = ", ".join(_clean(t) for t in tags if t)
    else:
        tags_text = _clean(tags)

    agency_address = ""
    if state:
        agency_address = f"Available in {state}"
    if details_url and not url:
        url = details_url

    return {
        "source": "granted_ai",
        "title": title or "Granted opportunity",
        "agency": funder,
        "agency_code": "",
        "agency_address": agency_address,
        "agency_contact": "",
        "agency_email": "",
        "agency_phone": "",
        "top_agency": _clean(row.get("source_type")),
        "deadline": deadline,
        "open_date": "",
        "eligibility": eligibility,
        "url": url or details_url,
        "description": summary[:1200],
        "opp_status": status or "active",
        "number": grant_id,
        "id": grant_id,
        "amount": amount,
        "award_ceiling": _format_amount_range(None, row.get("amount_max"), ""),
        "award_floor": _format_amount_range(row.get("amount_min"), None, ""),
        "doc_type": "granted",
        "alns": "",
        "funding_categories": tags_text,
        "funding_instruments": "",
        "cost_sharing": "",
        "number_of_awards": "",
        "match_reasons": reason_text,
        "details_url": details_url,
        "fit_score": fit_score if fit_score is not None else "",
        "state": state,
    }


def _cache_key(**parts: Any) -> str:
    return "|".join(str(parts.get(k, "")).strip().lower() for k in sorted(parts))


def _client() -> httpx.AsyncClient:
    return build_async_client(
        connect_timeout=CONNECT_TIMEOUT,
        read_timeout=READ_TIMEOUT,
        headers=_headers(),
        max_connections=4,
    )


async def _get_json_async(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
) -> dict[str, Any] | None:
    """GET with soft async retries. Never raises."""
    query = {k: v for k, v in params.items() if v not in (None, "")}
    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.get(url, params=query)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "GrantedAI timeout/connect (attempt %s/%s): %s",
                attempt,
                MAX_ATTEMPTS,
                last_error,
            )
            await asyncio.sleep(0.5 * attempt)
            continue
        except httpx.HTTPError as exc:
            logger.warning("GrantedAI request failed: %s", exc)
            return None

        if response.status_code == 200:
            body = json_body(response)
            if body is None:
                logger.warning("GrantedAI returned invalid JSON")
                return None
            if isinstance(body, dict) and body.get("error"):
                logger.warning("GrantedAI error payload: %s", body.get("error"))
                return None
            return body if isinstance(body, dict) else None

        if response.status_code == 429:
            logger.warning("GrantedAI rate limit reached (429)")
            return None

        if response.status_code in (500, 502, 503, 504):
            last_error = f"HTTP {response.status_code}"
            await asyncio.sleep(0.5 * attempt)
            continue

        logger.warning(
            "GrantedAI non-200: %s %s",
            response.status_code,
            response.text[:300],
        )
        return None

    logger.warning("GrantedAI unavailable after retries (%s)", last_error)
    return None


def _build_query(keyword: str, priority_area: str = "") -> str:
    parts = [_clean(keyword), _clean(priority_area)]
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(part)
    query = " ".join(out).strip()
    return query if len(query) >= 3 else (query or "grant funding")


async def search_grants_async(
    *,
    keyword: str = "",
    priority_area: str = "",
    location_city: str = "",
    location_state: str = "",
    org_type: str = "",
    limit: int = 10,
    use_discover: bool = True,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """
    Search GrantedAI for grants matching the user filters (async).

    Prefers /discover (AI + DB blend). Falls back to /grants on failure or empty.
    Never raises.
    """
    from services.location_utils import normalize_location

    location_city, location_state = normalize_location(location_city, location_state)
    query = _build_query(keyword, priority_area)
    if len(query) < 3:
        query = f"{query} funding".strip()
    effective_limit = max(1, min(int(limit or 10), 25))
    mapped_org = ORG_TYPE_MAP.get((org_type or "").strip().lower(), "") or _clean(org_type)

    key = _cache_key(
        q=query,
        state=location_state,
        org=mapped_org,
        city=location_city,
        limit=effective_limit,
        discover=use_discover,
    )
    cached = _CACHE.get(key)
    if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
        return list(cached[1])

    async def _run(active: httpx.AsyncClient) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        if use_discover:
            discover_params = {
                "q": query,
                "state": location_state.upper() if location_state else "",
                "org_type": mapped_org,
                "limit": str(effective_limit),
            }
            body = await _get_json_async(active, DISCOVER_URL, discover_params)
            rows = (body or {}).get("data") if body else None
            if isinstance(rows, list):
                results = [
                    _normalize_grant(row, from_discover=True)
                    for row in rows
                    if isinstance(row, dict)
                ][:effective_limit]

        if not results:
            grants_params = {
                "q": query,
                "status": "active",
                "state": location_state.upper() if location_state else "",
                "limit": str(effective_limit),
                "sort": "relevance",
            }
            body = await _get_json_async(active, GRANTS_URL, grants_params)
            rows = (body or {}).get("data") if body else None
            if isinstance(rows, list):
                results = [
                    _normalize_grant(row, from_discover=False)
                    for row in rows
                    if isinstance(row, dict)
                ][:effective_limit]

        if results:
            _CACHE[key] = (time.time(), results)
        return results

    if client is not None:
        return await _run(client)
    async with _client() as owned:
        return await _run(owned)


def search_grants(
    *,
    keyword: str = "",
    priority_area: str = "",
    location_city: str = "",
    location_state: str = "",
    org_type: str = "",
    limit: int = 10,
    use_discover: bool = True,
) -> list[dict[str, Any]]:
    """Sync bridge for search_grants_async()."""
    return run_sync(
        search_grants_async(
            keyword=keyword,
            priority_area=priority_area,
            location_city=location_city,
            location_state=location_state,
            org_type=org_type,
            limit=limit,
            use_discover=use_discover,
        )
    )
