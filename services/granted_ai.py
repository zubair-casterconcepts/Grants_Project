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

# Last good results per search. Every search calls GrantedAI; these are only used
# when that call fails or times out, so a brief outage does not blank the source.
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_FALLBACK_MAX_AGE_SECONDS = 3600


def _reuse_seconds() -> float:
    """
    How long an identical search may reuse earlier results instead of calling
    GrantedAI. Default 0: every search asks GrantedAI for current results.
    """
    try:
        return max(0.0, float(os.getenv("GRANTED_AI_CACHE_SECONDS", "0")))
    except ValueError:
        return 0.0

# When GrantedAI is down, /discover answers HTTP 504 after ~30s and /grants takes
# 25–55s, so one search could wait ~90s and Find Grants looked stuck. Cap the
# wait per search, and after a search that timed out with nothing, skip GrantedAI
# for a short while so the next searches don't wait on it again.
_UNAVAILABLE_UNTIL = {"at": 0.0}


def _env_seconds(name: str, default: float) -> float:
    try:
        return max(1.0, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _search_budget_seconds() -> float:
    # Healthy GrantedAI answers took 9–18s; 15s keeps a search (or its "no grants"
    # message) from waiting long on an outage. GRANTED_AI_TIMEOUT_SECONDS overrides.
    return _env_seconds("GRANTED_AI_TIMEOUT_SECONDS", 15)


def _cooldown_seconds() -> float:
    return _env_seconds("GRANTED_AI_COOLDOWN_SECONDS", 120)


def _task_body(task: asyncio.Task, name: str) -> dict[str, Any] | None:
    """Result of a finished request task; None if it was cancelled or failed."""
    if task.cancelled():
        return None
    exc = task.exception()
    if exc is not None:
        logger.warning("GrantedAI %s failed: %s", name, exc)
        return None
    return task.result()

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
        except httpx.TimeoutException as exc:
            # A timed-out GrantedAI call has not succeeded on retry; retrying only
            # doubled the wait and spent more of the daily request quota.
            logger.warning("GrantedAI timed out (%s); not retrying", type(exc).__name__)
            return None
        except httpx.ConnectError as exc:
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

        if response.status_code == 504:
            # Gateway timeout: GrantedAI's own server ran out of time. Same as above.
            logger.warning("GrantedAI gateway timeout (HTTP 504); not retrying")
            return None

        if response.status_code in (500, 502, 503):
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
    reuse_for = _reuse_seconds()
    cached = _CACHE.get(key)
    if reuse_for and cached and (time.time() - cached[0]) < reuse_for:
        return list(cached[1])

    def _stale_results(why: str) -> list[dict[str, Any]]:
        # Only after a real failure: GrantedAI is intermittent, so rather than
        # silently dropping the source, reuse this search's last good results if
        # they are recent. A successful empty answer is never replaced by these.
        stale = _CACHE.get(key)
        if stale and (time.time() - stale[0]) < _FALLBACK_MAX_AGE_SECONDS:
            logger.warning(
                "GrantedAI %s; serving results cached %.0f min ago",
                why,
                (time.time() - stale[0]) / 60,
            )
            return list(stale[1])
        return []

    skip_for = _UNAVAILABLE_UNTIL["at"] - time.time()
    if skip_for > 0:
        logger.info("GrantedAI skipped: it timed out recently (retrying in %.0fs)", skip_for)
        return []

    async def _run(active: httpx.AsyncClient) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []

        discover_params = {
            "q": query,
            "state": location_state.upper() if location_state else "",
            "org_type": mapped_org,
            "limit": str(effective_limit),
        }
        # The /grants endpoint rejects our former filters: `status=active` makes it
        # return HTTP 500 and `state=XX` makes it return no rows at all, which left
        # this fallback permanently empty. Query + sort work; location is enforced
        # afterwards by the eligibility gate's geography check.
        grants_params = {
            "q": query,
            "limit": str(effective_limit),
            "sort": "relevance",
        }

        # Kick both third-party GETs concurrently; prefer discover when it returns rows.
        # Wait no longer than the search budget — whatever answered in time is used.
        budget = _search_budget_seconds()
        timed_out = False
        failed = False
        if use_discover:
            discover_task = asyncio.create_task(
                _get_json_async(active, DISCOVER_URL, discover_params)
            )
            grants_task = asyncio.create_task(
                _get_json_async(active, GRANTS_URL, grants_params)
            )
            _, pending = await asyncio.wait({discover_task, grants_task}, timeout=budget)
            if pending:
                timed_out = True
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            discover_body = _task_body(discover_task, "discover")
            grants_body = _task_body(grants_task, "grants")
            failed = discover_body is None and grants_body is None

            rows = (discover_body or {}).get("data") if isinstance(discover_body, dict) else None
            if isinstance(rows, list):
                results = [
                    _normalize_grant(row, from_discover=True)
                    for row in rows
                    if isinstance(row, dict)
                ][:effective_limit]

            if not results:
                rows = (grants_body or {}).get("data") if isinstance(grants_body, dict) else None
                if isinstance(rows, list):
                    results = [
                        _normalize_grant(row, from_discover=False)
                        for row in rows
                        if isinstance(row, dict)
                    ][:effective_limit]
        else:
            try:
                body = await asyncio.wait_for(
                    _get_json_async(active, GRANTS_URL, grants_params), timeout=budget
                )
            except asyncio.TimeoutError:
                body = None
                timed_out = True
            failed = body is None
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

        if timed_out:
            cooldown = _cooldown_seconds()
            _UNAVAILABLE_UNTIL["at"] = time.time() + cooldown
            logger.warning(
                "GrantedAI did not answer within %.0fs; continuing without it "
                "and skipping it for the next %.0fs",
                budget,
                cooldown,
            )
            return _stale_results("timed out")
        if failed:
            return _stale_results("could not be reached")
        # GrantedAI answered but had nothing for this search: show nothing rather
        # than older results.
        return []

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
