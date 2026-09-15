"""Simpler.Grants.gov opportunity search client (API key required) — async httpx I/O.

Simpler.Grants.gov (https://api.simpler.grants.gov) is the new federal grants API.
One POST to /v1/opportunities/search returns each opportunity together with its
summary — deadline, award range, applicant types, funding categories and contact —
so no per-opportunity detail calls are needed. Rows are normalized to the same
shape and labels as services/grants_gov.py, so the freshness filter, eligibility
gate and scoring treat both sources the same way.

Set SIMPLER_GRANTS_API_KEY in .env (keys are created at
https://simpler.grants.gov/developer). Without a key this source returns no rows.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import time
from types import SimpleNamespace
from typing import Any

import httpx

from services.async_utils import build_async_client, json_body, run_sync
from services.grants_gov import (
    PRIORITY_AREA_TO_FUNDING_CATEGORY,
    _build_focus_keyword,
    _clean_text,
    _filter_by_location,
    _format_money,
)

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.simpler.grants.gov/v1/opportunities/search"
OPPORTUNITY_PAGE_URL = "https://simpler.grants.gov/opportunity/{opportunity_id}"
CONNECT_TIMEOUT_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 20
# 429 / 5xx are retried with exponential backoff, as the API guide asks.
MAX_ATTEMPTS = 3
# The API rejects a longer `query`.
QUERY_MAX_CHARS = 100

# Last good results per search. Every search calls the API; these are only used
# when that call fails, so a brief outage does not blank the source.
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_FALLBACK_MAX_AGE_SECONDS = 3600
_WARNED_NO_KEY = {"done": False}

# Grants.gov funding-category codes (services/grants_gov.py) → Simpler enum values.
_FUNDING_CATEGORY_BY_GRANTS_GOV_CODE = {
    "ED": "education",
    "HL": "health",
    "HO": "housing",
    "AR": "arts",
    "FN": "food_and_nutrition",
    "CD": "community_development",
    "ELT": "employment_labor_and_training",
    "ISS": "income_security_and_social_services",
    "LJL": "law_justice_and_legal_services",
    "BC": "business_and_commerce",
}

# Simpler returns enum values; Grants.gov returns these labels. Using the same
# labels keeps the applicant-type gate (services/eligibility.py) and category
# mapping (services/grant_categories.py) identical for both sources.
APPLICANT_TYPE_LABELS = {
    "state_governments": "State governments",
    "county_governments": "County governments",
    "city_or_township_governments": "City or township governments",
    "special_district_governments": "Special district governments",
    "independent_school_districts": "Independent school districts",
    "public_and_state_institutions_of_higher_education": (
        "Public and State controlled institutions of higher education"
    ),
    "private_institutions_of_higher_education": "Private institutions of higher education",
    "federally_recognized_native_american_tribal_governments": (
        "Native American tribal governments (Federally recognized)"
    ),
    "other_native_american_tribal_organizations": (
        "Native American tribal organizations (other than Federally recognized tribal governments)"
    ),
    "public_and_indian_housing_authorities": "Public and Indian housing authorities",
    "nonprofits_non_higher_education_with_501c3": (
        "Nonprofits having a 501(c)(3) status with the IRS, other than institutions of higher education"
    ),
    "nonprofits_non_higher_education_without_501c3": (
        "Nonprofits that do not have a 501(c)(3) status with the IRS, other than institutions of higher education"
    ),
    "individuals": "Individuals",
    "for_profit_organizations_other_than_small_businesses": (
        "For profit organizations other than small businesses"
    ),
    "small_businesses": "Small businesses",
    "other": 'Others (see text field entitled "Additional Information on Eligibility" for clarification)',
    "unrestricted": (
        "Unrestricted (i.e., open to any type of entity above), subject to any clarification "
        'in text field entitled "Additional Information on Eligibility"'
    ),
}

FUNDING_CATEGORY_LABELS = {
    "recovery_act": "Recovery Act",
    "agriculture": "Agriculture",
    "arts": 'Arts (see "Cultural Affairs" in CFDA)',
    "business_and_commerce": "Business and Commerce",
    "community_development": "Community Development",
    "consumer_protection": "Consumer Protection",
    "disaster_prevention_and_relief": "Disaster Prevention and Relief",
    "education": "Education",
    "employment_labor_and_training": "Employment, Labor and Training",
    "energy": "Energy",
    "environment": "Environment",
    "food_and_nutrition": "Food and Nutrition",
    "health": "Health",
    "housing": "Housing",
    "humanities": 'Humanities (see "Cultural Affairs" in CFDA)',
    "infrastructure_investment_and_jobs_act": "Infrastructure Investment and Jobs Act (IIJA)",
    "information_and_statistics": "Information and Statistics",
    "income_security_and_social_services": "Income Security and Social Services",
    "law_justice_and_legal_services": "Law, Justice and Legal Services",
    "natural_resources": "Natural Resources",
    "opportunity_zone_benefits": "Opportunity Zone Benefits",
    "regional_development": "Regional Development",
    "science_technology_and_other_research_and_development": (
        "Science and Technology and other Research and Development"
    ),
    "transportation": "Transportation",
    "affordable_care_act": "Affordable Care Act",
    "other": "Other",
    "energy_infrastructure_and_critical_mineral_and_materials": (
        "Energy Infrastructure and Critical Mineral and Materials (EICMM)"
    ),
    "recreation_and_tourism": "Recreation and Tourism",
}

FUNDING_INSTRUMENT_LABELS = {
    "cooperative_agreement": "Cooperative Agreement",
    "grant": "Grant",
    "procurement_contract": "Procurement Contract",
    "other": "Other",
}

_TAG_RE = re.compile(r"<[^>]+>")


def _api_key() -> str:
    return (os.getenv("SIMPLER_GRANTS_API_KEY") or "").strip()


def _reuse_seconds() -> float:
    """
    How long an identical search may reuse earlier results instead of calling the
    API. Default 0: every search asks Simpler.Grants.gov for current results.
    """
    try:
        return max(0.0, float(os.getenv("SIMPLER_GRANTS_CACHE_SECONDS", "0")))
    except ValueError:
        return 0.0


def _search_budget_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("SIMPLER_GRANTS_TIMEOUT_SECONDS", "15")))
    except ValueError:
        return 15.0


def _plain_text(value: Any, limit: int = 0) -> str:
    text = _TAG_RE.sub(" ", html.unescape(str(value or "")))
    return _clean_text(re.sub(r"\s+", " ", text), limit)


def _labels(values: Any, mapping: dict[str, str]) -> str:
    if not isinstance(values, list):
        return ""
    labels: list[str] = []
    for value in values:
        key = str(value or "").strip()
        if not key:
            continue
        label = mapping.get(key) or key.replace("_", " ").capitalize()
        if label not in labels:
            labels.append(label)
    return ", ".join(labels)


def _normalize_opportunity(item: dict[str, Any]) -> dict[str, Any]:
    """One Simpler.Grants.gov opportunity → the shared grant row shape."""
    summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
    opportunity_id = _clean_text(item.get("opportunity_id"))

    ceiling = _format_money(summary.get("award_ceiling"))
    floor = _format_money(summary.get("award_floor"))
    amount = ceiling or floor
    if ceiling and floor and ceiling != floor:
        amount = f"{floor} – {ceiling}"

    top_agency = _clean_text(item.get("top_level_agency_name"))
    agency_code = _clean_text(item.get("agency_code") or item.get("agency"))
    contact = _clean_text(summary.get("agency_contact_description"), 200)
    email = _clean_text(summary.get("agency_email_address"))
    address = " · ".join(part for part in (contact, email) if part) or " · ".join(
        part for part in (top_agency, agency_code) if part
    )

    cost_sharing = summary.get("is_cost_sharing")
    awards = summary.get("expected_number_of_awards")
    listings = item.get("opportunity_assistance_listings") or []

    return {
        "source": "simpler_grants",
        "title": _clean_text(item.get("opportunity_title")),
        "agency": _clean_text(item.get("agency_name")) or top_agency or agency_code,
        "agency_code": agency_code,
        "agency_address": address,
        "agency_contact": contact,
        "agency_email": email,
        "agency_phone": "",
        "top_agency": top_agency,
        "deadline": _clean_text(
            summary.get("close_date")
            or summary.get("forecasted_close_date")
            or summary.get("archive_date")
        ),
        "open_date": _clean_text(summary.get("post_date") or summary.get("forecasted_post_date")),
        "eligibility": _labels(summary.get("applicant_types"), APPLICANT_TYPE_LABELS),
        "url": OPPORTUNITY_PAGE_URL.format(opportunity_id=opportunity_id) if opportunity_id else "",
        "description": _plain_text(summary.get("summary_description"), 1200),
        "opp_status": _clean_text(item.get("opportunity_status")),
        "number": _clean_text(item.get("opportunity_number")),
        "id": opportunity_id,
        "amount": amount,
        "award_ceiling": ceiling,
        "award_floor": floor,
        "doc_type": "forecast" if summary.get("is_forecast") else "synopsis",
        "alns": ", ".join(
            _clean_text(listing.get("assistance_listing_number"))
            for listing in listings
            if isinstance(listing, dict) and listing.get("assistance_listing_number")
        ),
        "funding_categories": _labels(summary.get("funding_categories"), FUNDING_CATEGORY_LABELS),
        "funding_instruments": _labels(summary.get("funding_instruments"), FUNDING_INSTRUMENT_LABELS),
        "cost_sharing": "Yes" if cost_sharing is True else "No" if cost_sharing is False else "",
        "number_of_awards": "" if awards in (None, "") else str(awards),
    }


def _build_subject(
    keyword: str, priority_area: str, location_city: str, location_state: str
) -> SimpleNamespace:
    from services.location_utils import normalize_location

    city, state = normalize_location(location_city, location_state)
    return SimpleNamespace(
        title=(keyword or "").strip(),
        description="",
        priority_area=(priority_area or "").strip(),
        location_city=city,
        location_state=state,
    )


def _search_bodies(subject: SimpleNamespace, rows: int) -> list[dict[str, Any]]:
    """
    Request bodies to try, most specific first.

    Unlike Grants.gov's search2, Simpler's `query` must match every word, so a
    place name in it ("Education California") returns nothing. Place is left out
    of the query and checked locally instead, like Grants.gov's location filter.
    """
    page_size = max(1, min(int(rows or 25), 50))
    code = PRIORITY_AREA_TO_FUNDING_CATEGORY.get(subject.priority_area)
    category = _FUNDING_CATEGORY_BY_GRANTS_GOV_CODE.get(code or "")
    query = _build_focus_keyword(subject)[:QUERY_MAX_CHARS].strip()

    def body(search: str, funding_category: str) -> dict[str, Any]:
        filters: dict[str, Any] = {"opportunity_status": {"one_of": ["posted", "forecasted"]}}
        if funding_category:
            filters["funding_category"] = {"one_of": [funding_category]}
        pagination: dict[str, Any] = {"page_offset": 1, "page_size": page_size}
        request: dict[str, Any] = {"filters": filters, "pagination": pagination}
        if search:
            request["query"] = search
            pagination["sort_order"] = [{"order_by": "relevancy", "sort_direction": "descending"}]
        return request

    bodies: list[dict[str, Any]] = []
    if query and category:
        bodies.append(body(query, category))
    if query:
        bodies.append(body(query, ""))
    if category:
        bodies.append(body("", category))
    return bodies or [body("", "")]


async def _post_search_async(
    client: httpx.AsyncClient, body: dict[str, Any]
) -> list[dict[str, Any]] | None:
    """One search request. Returns rows, [] for no matches, or None on failure."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.post(SEARCH_URL, json=body)
        except httpx.TimeoutException:
            logger.warning("Simpler.Grants.gov search timed out")
            return None
        except httpx.HTTPError as exc:
            logger.warning("Simpler.Grants.gov request failed: %s", exc)
            return None

        status = response.status_code
        if status == 200:
            payload = json_body(response)
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list):
                logger.warning("Simpler.Grants.gov returned an unexpected response body")
                return None
            return [row for row in data if isinstance(row, dict)]
        if status in (401, 403):
            logger.error(
                "Simpler.Grants.gov rejected the API key (HTTP %s); check SIMPLER_GRANTS_API_KEY",
                status,
            )
            return None
        if status in (429, 500, 502, 503, 504) and attempt < MAX_ATTEMPTS:
            await asyncio.sleep(0.5 * 2 ** (attempt - 1))
            continue
        logger.warning("Simpler.Grants.gov search failed: HTTP %s %s", status, response.text[:300])
        return None
    return None


def _client() -> httpx.AsyncClient:
    return build_async_client(
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        read_timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"Content-Type": "application/json", "X-API-Key": _api_key()},
        max_connections=4,
    )


async def search_opportunities_async(
    *,
    keyword: str = "",
    priority_area: str = "",
    location_city: str = "",
    location_state: str = "",
    rows: int = 25,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """
    Search Simpler.Grants.gov for posted and forecasted opportunities (async).

    Returns normalized rows filtered by the user's place the same way as
    Grants.gov. Never raises; returns [] when the key is missing or the API fails.
    """
    if not _api_key():
        if not _WARNED_NO_KEY["done"]:
            logger.warning("SIMPLER_GRANTS_API_KEY is not set; skipping Simpler.Grants.gov")
            _WARNED_NO_KEY["done"] = True
        return []

    subject = _build_subject(keyword, priority_area, location_city, location_state)
    bodies = _search_bodies(subject, rows)
    cache_key = json.dumps(
        [bodies, subject.location_city.lower(), subject.location_state], sort_keys=True
    )
    reuse_for = _reuse_seconds()
    cached = _CACHE.get(cache_key)
    if reuse_for and cached and time.time() - cached[0] < reuse_for:
        return list(cached[1])

    async def _run(active: httpx.AsyncClient) -> list[dict[str, Any]] | None:
        # Try the broader request when a narrower one has no hits — or only hits
        # that the location filter removes — so a specific query cannot hide
        # opportunities the category search would have found.
        for body in bodies:
            found = await _post_search_async(active, body)
            if found is None:
                return None
            normalized = [_normalize_opportunity(hit) for hit in found]
            kept = _filter_by_location(normalized, subject)[:30]
            if kept:
                return kept
        return []

    async def _within_budget(active: httpx.AsyncClient) -> list[dict[str, Any]] | None:
        budget = _search_budget_seconds()
        try:
            return await asyncio.wait_for(_run(active), timeout=budget)
        except asyncio.TimeoutError:
            logger.warning("Simpler.Grants.gov did not answer within %.0fs; continuing without it", budget)
        except Exception:
            logger.warning("Simpler.Grants.gov search failed", exc_info=True)
        return None

    if client is not None:
        results = await _within_budget(client)
    else:
        async with _client() as owned:
            results = await _within_budget(owned)

    if results is None:
        stale = _CACHE.get(cache_key)
        if stale and time.time() - stale[0] < _FALLBACK_MAX_AGE_SECONDS:
            logger.warning(
                "Simpler.Grants.gov unavailable; serving results cached %.0f min ago",
                (time.time() - stale[0]) / 60,
            )
            return list(stale[1])
        return []

    _CACHE[cache_key] = (time.time(), results)
    return list(results)


def search_opportunities(
    *,
    keyword: str = "",
    priority_area: str = "",
    location_city: str = "",
    location_state: str = "",
    rows: int = 25,
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
