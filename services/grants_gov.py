"""Grants.gov search2 client (no API key required) — async httpx I/O."""

from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Any

import httpx

from services.async_utils import build_async_client, json_body, run_sync

logger = logging.getLogger(__name__)

SEARCH2_URL = "https://api.grants.gov/v1/api/search2"
FETCH_OPPORTUNITY_URL = "https://api.grants.gov/v1/api/fetchOpportunity"
# Max concurrent detail lookups during enrichment.
DETAIL_WORKERS = 8
# Only enrich the first N hits so search can stream cards sooner.
# Remaining rows still return with search2 fields (title, deadline, url, status).
ENRICH_LIMIT = 10
DETAIL_TIMEOUT_SECONDS = 8
CONNECT_TIMEOUT_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 20

# Only map known priority areas — never guess.
PRIORITY_AREA_TO_FUNDING_CATEGORY = {
    "Education": "ED",
    "Literacy": "ED",
    "Health": "HL",
    "Housing": "HO",
    "Arts": "AR",
    "Culture": "AR",
    "Food Access": "FN",
    "Community Development": "CD",
    "Workforce Development": "ELT",
    "Human Services": "ISS",
    "Public Safety": "LJL",
    "Economic Development": "BC",
}

US_STATE_NAMES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "DC": "District of Columbia",
}


def _build_focus_keyword(project: Any) -> str:
    title = (getattr(project, "title", None) or "").strip()
    if title:
        return title
    description = (getattr(project, "description", None) or "").strip()
    if not description:
        return ""
    words = description.split()
    return " ".join(words[:8])


def _location_fields(project: Any) -> tuple[str, str, str]:
    city = (getattr(project, "location_city", None) or "").strip()
    state = (getattr(project, "location_state", None) or "").strip().upper()
    state_name = US_STATE_NAMES.get(state, "")
    return city, state, state_name


def _location_search_terms(project: Any) -> list[str]:
    city, state, state_name = _location_fields(project)
    terms: list[str] = []
    if city:
        terms.append(city)
    if state_name:
        terms.append(state_name)
    elif state:
        terms.append(state)
    return terms


def _dedupe_parts(parts: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        key = part.lower()
        if not part or key in seen:
            continue
        seen.add(key)
        unique.append(part)
    return unique


def _build_keyword(project: Any) -> str:
    """Focus keyword + user location (search2 has no dedicated location field)."""
    return " ".join(
        _dedupe_parts([_build_focus_keyword(project), *_location_search_terms(project)])
    )


def _compile_term_patterns(terms: list[str]) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for term in terms:
        cleaned = term.strip()
        if not cleaned:
            continue
        if len(cleaned) == 2 and cleaned.isalpha():
            patterns.append(
                re.compile(rf"(?<![A-Za-z]){re.escape(cleaned)}(?![A-Za-z])", re.IGNORECASE)
            )
        else:
            patterns.append(re.compile(rf"\b{re.escape(cleaned)}\b", re.IGNORECASE))
    return patterns


def _hit_text(hit: dict[str, Any]) -> str:
    return html.unescape(
        " ".join(
            str(hit.get(key) or "")
            for key in ("title", "agencyName", "agency", "agencyCode", "number")
        )
    )


def _clean_text(value: Any, limit: int = 0) -> str:
    text = html.unescape(str(value or "")).strip()
    if limit and len(text) > limit:
        return text[:limit].rstrip()
    return text


def _format_money(value: Any) -> str:
    if value in (None, ""):
        return ""
    raw = str(value).strip()
    if not raw or raw.lower() in {"none", "null", "n/a", "na", "tbd"}:
        return ""
    try:
        amount = float(raw.replace(",", "").replace("$", ""))
        if amount.is_integer():
            return f"${amount:,.0f}"
        return f"${amount:,.2f}"
    except (TypeError, ValueError):
        return raw


def _join_labels(items: Any, key: str = "description") -> str:
    if not isinstance(items, list):
        return ""
    labels: list[str] = []
    for item in items:
        if isinstance(item, dict):
            label = item.get(key) or item.get("label") or item.get("id") or ""
        else:
            label = item
        text = _clean_text(label)
        if text and text not in labels:
            labels.append(text)
    return ", ".join(labels)


def _build_agency_address(synopsis: dict[str, Any]) -> str:
    """Build a provider location/contact line from Grants.gov detail fields."""
    parts: list[str] = []
    address = _clean_text(synopsis.get("agencyAddressDesc"))
    phone = _clean_text(
        synopsis.get("agencyContactPhone") or synopsis.get("agencyPhone")
    )
    email = _clean_text(synopsis.get("agencyContactEmail"))
    contact = _clean_text(synopsis.get("agencyContactName"))

    # agencyAddressDesc is sometimes an email; keep useful contact lines.
    if address and "@" not in address:
        parts.append(address)
    if contact and contact.lower() not in {p.lower() for p in parts}:
        parts.append(contact)
    if email and email.lower() not in {p.lower() for p in parts}:
        parts.append(email)
    elif address and "@" in address and address.lower() not in {p.lower() for p in parts}:
        parts.append(address)
    if phone:
        parts.append(f"Phone: {phone}")
    return " · ".join(parts)


def _mentions_any(text: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _other_state_patterns(user_state: str) -> list[re.Pattern[str]]:
    """Patterns for every US state except the user's."""
    patterns: list[re.Pattern[str]] = []
    user_state = (user_state or "").upper()
    for abbr, name in US_STATE_NAMES.items():
        if abbr == user_state:
            continue
        patterns.append(re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE))
        patterns.append(
            re.compile(rf"(?<![A-Za-z]){re.escape(abbr)}(?![A-Za-z])", re.IGNORECASE)
        )
    return patterns


def _filter_by_location(hits: list[dict[str, Any]], project: Any) -> list[dict[str, Any]]:
    """
    Keep opportunities that fit the user's place:
    - Prefer hits that mention the user's city/state
    - Drop hits that clearly name a different US state
    - Keep national/generic hits (no other-state mention) for the user's area
    """
    city, state, state_name = _location_fields(project)
    if not city and not state:
        return hits

    user_patterns = _compile_term_patterns(
        [term for term in (city, state_name, state) if term]
    )
    other_patterns = _other_state_patterns(state) if state else []

    local_hits: list[dict[str, Any]] = []
    national_hits: list[dict[str, Any]] = []

    for hit in hits:
        text = _hit_text(hit)
        mentions_user = _mentions_any(text, user_patterns) if user_patterns else False
        mentions_other = _mentions_any(text, other_patterns) if other_patterns else False

        if mentions_user:
            local_hits.append(hit)
            continue
        if mentions_other:
            # Clearly tied to another state — exclude for this user.
            continue
        national_hits.append(hit)

    # Location-first ordering: local mentions, then national/open opportunities.
    return local_hits + national_hits


def _funding_category_for_project(project: Any) -> str | None:
    priority = (getattr(project, "priority_area", None) or "").strip()
    return PRIORITY_AREA_TO_FUNDING_CATEGORY.get(priority)


def _normalize_hit(hit: dict[str, Any]) -> dict[str, Any]:
    hit_id = str(hit.get("id") or "")
    agency = (
        hit.get("agencyName")
        or hit.get("agency")
        or hit.get("agencyCode")
        or ""
    )
    alns = hit.get("cfdaList") or hit.get("alnList") or hit.get("alnist") or []
    if not isinstance(alns, list):
        alns = []
    return {
        "source": "grants_gov",
        "title": _clean_text(hit.get("title")),
        "agency": _clean_text(agency),
        "agency_code": _clean_text(hit.get("agencyCode")),
        "agency_address": "",
        "agency_contact": "",
        "agency_email": "",
        "agency_phone": "",
        "top_agency": "",
        "deadline": _clean_text(hit.get("closeDate")),
        "open_date": _clean_text(hit.get("openDate")),
        "eligibility": "",
        "url": f"https://www.grants.gov/search-results-detail/{hit_id}" if hit_id else "",
        "description": "",
        "opp_status": _clean_text(hit.get("oppStatus")),
        "number": _clean_text(hit.get("number")),
        "id": hit_id,
        "amount": "",
        "award_ceiling": "",
        "award_floor": "",
        "doc_type": _clean_text(hit.get("docType")),
        "alns": ", ".join(_clean_text(x) for x in alns if x),
        "funding_categories": "",
        "funding_instruments": "",
        "cost_sharing": "",
        "number_of_awards": "",
    }


async def fetch_opportunity_detail_async(
    client: httpx.AsyncClient,
    opportunity_id: str | int,
) -> dict[str, Any]:
    """Fetch full Grants.gov opportunity details. Never raises."""
    if not opportunity_id:
        return {}
    try:
        payload_id: Any = (
            int(opportunity_id) if str(opportunity_id).isdigit() else opportunity_id
        )
        response = await client.post(
            FETCH_OPPORTUNITY_URL,
            json={"opportunityId": payload_id},
            headers={"Content-Type": "application/json"},
            timeout=httpx.Timeout(
                connect=CONNECT_TIMEOUT_SECONDS,
                read=DETAIL_TIMEOUT_SECONDS,
                write=DETAIL_TIMEOUT_SECONDS,
                pool=CONNECT_TIMEOUT_SECONDS,
            ),
        )
    except httpx.HTTPError:
        logger.warning("Grants.gov fetchOpportunity network error for %s", opportunity_id)
        return {}

    if response.status_code != 200:
        logger.warning(
            "Grants.gov fetchOpportunity non-200 for %s: %s",
            opportunity_id,
            response.status_code,
        )
        return {}

    body = json_body(response)
    if not isinstance(body, dict):
        return {}

    data = body.get("data")
    return data if isinstance(data, dict) else {}


async def _fetch_detail_standalone(opportunity_id: str | int) -> dict[str, Any]:
    async with _search_client() as client:
        return await fetch_opportunity_detail_async(client, opportunity_id)


def fetch_opportunity_detail(opportunity_id: str | int) -> dict[str, Any]:
    """Sync bridge for fetch_opportunity_detail_async()."""
    return run_sync(_fetch_detail_standalone(opportunity_id))


def _detail_body(detail: dict[str, Any]) -> dict[str, Any]:
    """Prefer synopsis details; fall back to forecast for forecasted opportunities."""
    synopsis = detail.get("synopsis")
    if isinstance(synopsis, dict) and synopsis:
        return synopsis
    forecast = detail.get("forecast")
    if isinstance(forecast, dict) and forecast:
        return forecast
    return {}


def _enrich_from_detail(base: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    if not detail:
        return base

    body = _detail_body(detail)
    agency_details = detail.get("agencyDetails") if isinstance(detail.get("agencyDetails"), dict) else {}
    top_agency = detail.get("topAgencyDetails") if isinstance(detail.get("topAgencyDetails"), dict) else {}

    agency_name = _clean_text(
        body.get("agencyName")
        or agency_details.get("agencyName")
        or base.get("agency")
    )
    top_agency_name = _clean_text(top_agency.get("agencyName"))
    ceiling = _format_money(body.get("awardCeiling") or body.get("awardCeilingFormatted"))
    floor = _format_money(body.get("awardFloor") or body.get("awardFloorFormatted"))
    amount = ceiling or floor or base.get("amount") or ""
    if ceiling and floor and ceiling != floor:
        amount = f"{floor} – {ceiling}"

    cfdas = detail.get("cfdas") or []
    aln_text = base.get("alns") or ""
    if isinstance(cfdas, list) and cfdas:
        aln_parts = []
        for item in cfdas:
            if isinstance(item, dict):
                aln_parts.append(
                    _clean_text(item.get("cfdaNumber") or item.get("alnNumber") or item.get("id"))
                )
            else:
                aln_parts.append(_clean_text(item))
        aln_text = ", ".join(p for p in aln_parts if p) or aln_text

    cost_sharing = body.get("costSharing")
    if isinstance(cost_sharing, bool):
        cost_sharing_text = "Yes" if cost_sharing else "No"
    else:
        cost_sharing_text = _clean_text(cost_sharing)

    description = _clean_text(
        body.get("synopsisDesc") or body.get("forecastDesc") or body.get("description"),
        1200,
    )

    row = dict(base)
    row.update(
        {
            "title": _clean_text(detail.get("opportunityTitle")) or base.get("title") or "",
            "number": _clean_text(detail.get("opportunityNumber")) or base.get("number") or "",
            "agency": agency_name,
            "agency_code": _clean_text(
                body.get("agencyCode")
                or agency_details.get("agencyCode")
                or base.get("agency_code")
            ),
            "top_agency": top_agency_name,
            "agency_address": _build_agency_address(body),
            "agency_contact": _clean_text(body.get("agencyContactName")),
            "agency_email": _clean_text(body.get("agencyContactEmail")),
            "agency_phone": _clean_text(
                body.get("agencyContactPhone") or body.get("agencyPhone")
            ),
            "description": description,
            "eligibility": _join_labels(body.get("applicantTypes")),
            "funding_categories": _join_labels(body.get("fundingActivityCategories")),
            "funding_instruments": _join_labels(body.get("fundingInstruments")),
            "award_ceiling": ceiling,
            "award_floor": floor,
            "amount": amount,
            "open_date": _clean_text(
                body.get("postingDate") or body.get("estimatedPostDate") or base.get("open_date")
            ),
            "deadline": _clean_text(
                body.get("responseDate")
                or body.get("estimatedResponseDate")
                or body.get("archiveDate")
                or base.get("deadline")
            ),
            "number_of_awards": _clean_text(body.get("numberOfAwards")),
            "cost_sharing": cost_sharing_text,
            "alns": aln_text,
            "doc_type": _clean_text(detail.get("docType") or base.get("doc_type")),
        }
    )
    # Always surface department-level provider when available.
    if not row.get("agency_address") and top_agency_name:
        code = _clean_text(agency_details.get("agencyCode") or row.get("agency_code"))
        row["agency_address"] = " · ".join(
            part for part in (top_agency_name, code) if part
        )
    return row


async def _enrich_hits_async(
    client: httpx.AsyncClient,
    normalized: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach full opportunity details concurrently; never fail the search."""
    if not normalized:
        return normalized

    enriched = list(normalized)
    index_by_id = {
        str(item.get("id")): idx
        for idx, item in enumerate(enriched)
        if item.get("id")
    }
    if not index_by_id:
        return enriched

    semaphore = asyncio.Semaphore(min(DETAIL_WORKERS, len(index_by_id)))

    async def _detail(opp_id: str) -> tuple[str, dict[str, Any]]:
        async with semaphore:
            return opp_id, await fetch_opportunity_detail_async(client, opp_id)

    results = await asyncio.gather(
        *(_detail(opp_id) for opp_id in index_by_id),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            logger.warning("Grants.gov detail enrich failed: %s", result)
            continue
        opp_id, detail = result
        idx = index_by_id.get(str(opp_id))
        if idx is None or not detail:
            continue
        enriched[idx] = _enrich_from_detail(enriched[idx], detail)
    return enriched


async def _post_search2_async(
    client: httpx.AsyncClient,
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    try:
        response = await client.post(
            SEARCH2_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
    except httpx.HTTPError:
        logger.exception("Grants.gov search2 network error")
        return []

    if response.status_code != 200:
        logger.error(
            "Grants.gov search2 non-200 response: %s %s",
            response.status_code,
            response.text[:500],
        )
        return []

    body = json_body(response)
    if not isinstance(body, dict):
        logger.error("Grants.gov search2 returned invalid JSON")
        return []

    data = body.get("data") or {}
    hits = data.get("oppHits") or []
    if not isinstance(hits, list):
        logger.error("Grants.gov search2 oppHits was not a list: %r", type(hits))
        return []

    return [hit for hit in hits if isinstance(hit, dict)]


def _search_client() -> httpx.AsyncClient:
    return build_async_client(
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        read_timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"Content-Type": "application/json"},
        max_connections=DETAIL_WORKERS + 2,
    )


def _build_search_request(
    *,
    keyword: str,
    priority_area: str,
    location_city: str,
    location_state: str,
    rows: int,
) -> tuple[dict[str, Any], Any, list[str]]:
    """Build the search2 payload plus the subject/location terms used for filtering."""
    from types import SimpleNamespace

    from services.location_utils import normalize_location

    location_city, location_state = normalize_location(location_city, location_state)
    subject = SimpleNamespace(
        title=(keyword or "").strip(),
        description="",
        priority_area=(priority_area or "").strip(),
        location_city=location_city,
        location_state=location_state,
    )
    location_terms = _location_search_terms(subject)
    effective_rows = max(1, min(int(rows or 25), 50))
    if location_terms:
        effective_rows = max(effective_rows, 25)

    payload: dict[str, Any] = {
        "rows": effective_rows,
        "keyword": _build_keyword(subject),
        "oppNum": "",
        "eligibilities": "",
        "agencies": "",
        "oppStatuses": "forecasted|posted",
        "aln": "",
        "fundingCategories": "",
    }

    funding_category = _funding_category_for_project(subject)
    if funding_category:
        payload["fundingCategories"] = funding_category

    return payload, subject, location_terms


async def search_with_filters_async(
    *,
    keyword: str = "",
    priority_area: str = "",
    location_city: str = "",
    location_state: str = "",
    rows: int = 25,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch Grants.gov opportunities with explicit filters (async).

    Cleans/normalizes hits and applies location filters. Used by the AI agent tool
    and by search_opportunities().
    """
    payload, subject, location_terms = _build_search_request(
        keyword=keyword,
        priority_area=priority_area,
        location_city=location_city,
        location_state=location_state,
        rows=rows,
    )

    async def _run(active: httpx.AsyncClient) -> list[dict[str, Any]]:
        hits = await _post_search2_async(active, payload)

        # If focus+location is too narrow, retry with location + category.
        if not hits and location_terms:
            retry = dict(payload)
            retry["keyword"] = " ".join(location_terms)
            hits = await _post_search2_async(active, retry)

        filtered = _filter_by_location(hits, subject)
        # Keep a wider pre-enrich pool; freshness filtering happens in grant_agent.
        normalized = [_normalize_hit(hit) for hit in filtered][:30]
        # Enrich only the leading slice so the async tool can return (and the UI
        # can paint first cards) without waiting on every detail lookup.
        head = normalized[:ENRICH_LIMIT]
        tail = normalized[ENRICH_LIMIT:]
        enriched_head = await _enrich_hits_async(active, head)
        return enriched_head + tail

    if client is not None:
        return await _run(client)
    async with _search_client() as owned:
        return await _run(owned)


def search_with_filters(
    *,
    keyword: str = "",
    priority_area: str = "",
    location_city: str = "",
    location_state: str = "",
    rows: int = 25,
) -> list[dict[str, Any]]:
    """Sync bridge for search_with_filters_async()."""
    return run_sync(
        search_with_filters_async(
            keyword=keyword,
            priority_area=priority_area,
            location_city=location_city,
            location_state=location_state,
            rows=rows,
        )
    )


def _opportunity_search_args(project: Any) -> dict[str, Any]:
    title = (getattr(project, "title", None) or "").strip()
    description = (getattr(project, "description", None) or "").strip()
    return {
        "keyword": title or " ".join(description.split()[:8]),
        "priority_area": (getattr(project, "priority_area", None) or ""),
        "location_city": (getattr(project, "location_city", None) or ""),
        "location_state": (getattr(project, "location_state", None) or ""),
        "rows": 25,
    }


async def search_opportunities_async(project: Any) -> list[dict[str, Any]]:
    """Async search for a project/profile-like object."""
    return await search_with_filters_async(**_opportunity_search_args(project))


def search_opportunities(project: Any) -> list[dict[str, Any]]:
    """
    Search Grants.gov for opportunities matching a project/profile-like object.

    Uses: title/description, priority_area (category), location_city/state.
    """
    return search_with_filters(**_opportunity_search_args(project))
