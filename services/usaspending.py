"""USASpending.gov API client (no auth) — async httpx, resilient to flaky networks."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import quote

import httpx

from services.async_utils import build_async_client, json_body, run_sync

logger = logging.getLogger(__name__)

BASE_URL = "https://api.usaspending.gov"
SPENDING_BY_AWARD_URL = f"{BASE_URL}/api/v2/search/spending_by_award/"

# (connect timeout, read timeout) — SSL handshakes on this API can be slow.
CONNECT_TIMEOUT = 8
READ_TIMEOUT = 35
MAX_ATTEMPTS = 3

# Prefer project / cooperative grants; include formula/block as secondary.
GRANT_AWARD_TYPE_CODES = ["04", "05", "03", "02"]

AWARD_FIELDS = [
    "Award ID",
    "Recipient Name",
    "Start Date",
    "End Date",
    "Award Amount",
    "Description",
    "Awarding Agency",
    "Awarding Sub Agency",
    "Award Type",
    "Funding Agency",
    "Place of Performance City Name",
    "Place of Performance State Code",
    "Place of Performance Country Code",
    "Recipient City Name",
    "Recipient State Code",
]

# Short-lived cache so dashboard refreshes don't re-hit a slow API every time.
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_TTL_SECONDS = 300


def _client() -> httpx.AsyncClient:
    """Async client with connection-level retries; status retries handled below."""
    return build_async_client(
        connect_timeout=CONNECT_TIMEOUT,
        read_timeout=READ_TIMEOUT,
        headers={"Content-Type": "application/json"},
        retries=2,
        max_connections=4,
    )


def _build_keywords(
    keyword: str,
    priority_area: str = "",
    location_city: str = "",
) -> list[str]:
    # Keep the keyword list short — long filters make USASpending slower / flakier.
    candidates = [
        (keyword or "").strip(),
        (priority_area or "").strip(),
        (location_city or "").strip(),
    ]
    seen: set[str] = set()
    out: list[str] = []
    for w in candidates:
        if not w:
            continue
        key = w.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
        if len(out) >= 2:
            break
    return out or ["grant"]


def _location_filters(location_state: str = "") -> list[dict[str, str]]:
    state = (location_state or "").strip().upper()
    if not state:
        return []
    return [{"country": "USA", "state": state}]


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _location_line(*parts: Any) -> str:
    cleaned = [p for p in (_clean(part) for part in parts) if p]
    # Deduplicate while preserving order.
    unique: list[str] = []
    seen: set[str] = set()
    for part in cleaned:
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(part)
    return ", ".join(unique)


def _normalize_award(row: dict[str, Any]) -> dict[str, Any]:
    award_id = _clean(row.get("Award ID") or row.get("award_id"))
    internal_id = _clean(
        row.get("generated_internal_award_id")
        or row.get("generated_internal_id")
    )
    detail_path = internal_id or award_id
    url = (
        f"https://www.usaspending.gov/award/{quote(detail_path, safe='')}"
        if detail_path
        else "https://www.usaspending.gov/"
    )
    amount = row.get("Award Amount")
    amount_text = ""
    if isinstance(amount, (int, float)):
        amount_text = f"${amount:,.0f}"
    elif amount not in (None, ""):
        amount_text = str(amount)

    description = _clean(row.get("Description"))
    recipient = _clean(row.get("Recipient Name"))
    awarding_agency = _clean(row.get("Awarding Agency"))
    awarding_sub = _clean(row.get("Awarding Sub Agency"))
    funding_agency = _clean(row.get("Funding Agency"))
    agency = awarding_agency or funding_agency or awarding_sub or recipient
    top_agency = funding_agency if funding_agency and funding_agency != agency else ""

    pop_city = _clean(row.get("Place of Performance City Name"))
    pop_state = _clean(row.get("Place of Performance State Code"))
    pop_country = _clean(row.get("Place of Performance Country Code"))
    recipient_city = _clean(row.get("Recipient City Name"))
    recipient_state = _clean(row.get("Recipient State Code"))

    # USASpending search does not expose a street address; use performance/recipient place.
    agency_address = _location_line(pop_city, pop_state, pop_country)
    if not agency_address:
        agency_address = _location_line(recipient_city, recipient_state)
    if agency_address:
        agency_address = f"Place of performance: {agency_address}"

    title = description[:140] if description else (recipient or award_id or "USASpending award")

    return {
        "source": "usaspending",
        "title": title,
        "agency": agency,
        "agency_code": "",
        "agency_address": agency_address,
        "agency_contact": recipient,
        "agency_email": "",
        "agency_phone": "",
        "top_agency": top_agency or awarding_sub,
        "deadline": _clean(row.get("End Date")),
        "open_date": _clean(row.get("Start Date")),
        "eligibility": "",
        "url": url,
        "description": description[:1200] if description else "",
        "opp_status": _clean(row.get("Award Type")) or "award",
        "number": award_id,
        "id": internal_id or award_id,
        "amount": amount_text,
        "award_ceiling": amount_text,
        "award_floor": "",
        "start_date": _clean(row.get("Start Date")),
        "pop_city": pop_city,
        "pop_state": pop_state,
        "pop_country": pop_country,
        "recipient": recipient,
        "awarding_sub_agency": awarding_sub,
        "funding_agency": funding_agency,
        "doc_type": "award",
        "alns": "",
        "funding_categories": "",
        "funding_instruments": _clean(row.get("Award Type")),
        "cost_sharing": "",
        "number_of_awards": "",
    }


def _cache_key(
    keyword: str,
    priority_area: str,
    location_city: str,
    location_state: str,
    limit: int,
) -> str:
    return "|".join(
        [
            keyword.strip().lower(),
            priority_area.strip().lower(),
            location_city.strip().lower(),
            location_state.strip().upper(),
            str(limit),
        ]
    )


async def _post_with_retries_async(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    """POST with soft async retries. Never raises — returns JSON body or None."""
    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.post(SPENDING_BY_AWARD_URL, json=payload)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "USASpending timeout/connect (attempt %s/%s): %s",
                attempt,
                MAX_ATTEMPTS,
                last_error,
            )
            await asyncio.sleep(0.6 * attempt)
            continue
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("USASpending request failed: %s", last_error)
            return None

        if response.status_code == 200:
            body = json_body(response)
            if body is None:
                logger.warning("USASpending returned invalid JSON")
            return body

        # Retry only on transient statuses.
        if response.status_code in (429, 500, 502, 503, 504):
            last_error = f"HTTP {response.status_code}"
            logger.warning(
                "USASpending transient HTTP %s (attempt %s/%s)",
                response.status_code,
                attempt,
                MAX_ATTEMPTS,
            )
            await asyncio.sleep(0.6 * attempt)
            continue

        logger.warning(
            "USASpending non-200 response: %s %s",
            response.status_code,
            response.text[:300],
        )
        return None

    logger.warning("USASpending unavailable after retries (%s)", last_error)
    return None


def _build_payloads(
    *,
    keywords: list[str],
    locations: list[dict[str, str]],
    limit: int,
) -> list[dict[str, Any]]:
    """
    Progressive, lighter payloads. First try is best-quality filters;
    later tries are simpler so slow networks still return something.
    """
    base_fields = [
        "Award ID",
        "Recipient Name",
        "End Date",
        "Award Amount",
        "Description",
        "Awarding Agency",
        "Awarding Sub Agency",
        "Funding Agency",
        "Award Type",
        "Place of Performance City Name",
        "Place of Performance State Code",
        "Place of Performance Country Code",
    ]
    payloads: list[dict[str, Any]] = []

    full_filters: dict[str, Any] = {
        "keywords": keywords,
        "award_type_codes": GRANT_AWARD_TYPE_CODES,
    }
    if locations:
        full_filters["place_of_performance_locations"] = locations
    payloads.append(
        {
            "filters": full_filters,
            "fields": AWARD_FIELDS,
            "limit": limit,
            "page": 1,
            "sort": "Award Amount",
            "order": "desc",
            "subawards": False,
        }
    )

    # Lighter: project grants only + state.
    light_filters: dict[str, Any] = {
        "keywords": keywords[:1],
        "award_type_codes": ["04", "05"],
    }
    if locations:
        light_filters["place_of_performance_locations"] = locations
    payloads.append(
        {
            "filters": light_filters,
            "fields": base_fields,
            "limit": min(limit, 8),
            "page": 1,
            "sort": "Award Amount",
            "order": "desc",
            "subawards": False,
        }
    )

    # Minimal: keyword + state + required award_type_codes (API rejects without it).
    minimal_filters: dict[str, Any] = {
        "keywords": keywords[:1],
        "award_type_codes": ["04", "05"],
    }
    if locations:
        minimal_filters["place_of_performance_locations"] = locations
    payloads.append(
        {
            "filters": minimal_filters,
            "fields": base_fields,
            "limit": min(limit, 8),
            "page": 1,
            "sort": "Award Amount",
            "order": "desc",
            "subawards": False,
        }
    )
    return payloads


async def search_awards_async(
    *,
    keyword: str = "",
    priority_area: str = "",
    location_city: str = "",
    location_state: str = "",
    limit: int = 10,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """
    Search USASpending awards by keyword + user location (async).

    Never raises. On network/SSL timeouts returns [] so the agent/dashboard
    can continue with Grants.gov results alone.
    """
    effective_limit = max(1, min(int(limit or 10), 20))
    key = _cache_key(keyword, priority_area, location_city, location_state, effective_limit)
    cached = _CACHE.get(key)
    if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
        return list(cached[1])

    from services.location_utils import normalize_location

    location_city, location_state = normalize_location(location_city, location_state)
    keywords = _build_keywords(keyword, priority_area, location_city)
    locations = _location_filters(location_state)
    payloads = _build_payloads(
        keywords=keywords,
        locations=locations,
        limit=effective_limit,
    )

    async def _run(active: httpx.AsyncClient) -> list[dict[str, Any]]:
        for payload in payloads:
            body = await _post_with_retries_async(active, payload)
            if not body:
                continue
            rows = body.get("results") or []
            if not isinstance(rows, list) or not rows:
                continue
            results = [
                _normalize_award(row) for row in rows if isinstance(row, dict)
            ][:10]
            if results:
                _CACHE[key] = (time.time(), results)
                return results
        return []

    if client is not None:
        return await _run(client)
    async with _client() as owned:
        return await _run(owned)


def search_awards(
    *,
    keyword: str = "",
    priority_area: str = "",
    location_city: str = "",
    location_state: str = "",
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Sync bridge for search_awards_async()."""
    return run_sync(
        search_awards_async(
            keyword=keyword,
            priority_area=priority_area,
            location_city=location_city,
            location_state=location_state,
            limit=limit,
        )
    )
