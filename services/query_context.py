"""
Merge saved profile defaults with per-message user overrides for tool calls.

Profile values are defaults. Anything the user states in the current query
(e.g. "find grants in California") overrides only those fields for this search.
"""

from __future__ import annotations

import re
from typing import Any

from services.location_utils import (
    CITY_STATE_HINTS,
    US_STATE_NAMES,
    location_from_profile,
    normalize_location,
)

PRIORITY_AREAS = (
    "Arts",
    "Community Development",
    "Culture",
    "Downtown Development",
    "Economic Development",
    "Education",
    "Food Access",
    "Health",
    "Housing",
    "Human Services",
    "Literacy",
    "Public Safety",
    "Recreation",
    "Workforce Development",
    "Youth Development",
)

ORG_TYPE_ALIASES = {
    "501c3": "501c3",
    "501(c)(3)": "501c3",
    "nonprofit": "501c3",
    "non-profit": "501c3",
    "non profit": "501c3",
    "government": "government",
    "school": "school",
    "schools": "school",
    "other": "other",
}

_BOILERPLATE_RE = re.compile(
    r"\b("
    r"find|search|show|get|match|looking for|look for|please|can you|"
    r"grants?|funding|opportunities|opportunity|again|me|for me|"
    r"in|near|around|at|within|from|about|related to|regarding|"
    r"budget|under|over|upto|up to|around|greater than|more than|"
    r"this|that|would|be|with|the|a|an|and|or|to|of|for"
    r")\b",
    re.I,
)

_KEYWORD_STOPWORDS = {
    "the",
    "a",
    "an",
    "this",
    "that",
    "would",
    "be",
    "with",
    "and",
    "or",
    "to",
    "of",
    "for",
    "in",
    "on",
}


def profile_defaults(profile: Any) -> dict[str, Any]:
    if isinstance(profile, dict):
        city = str(profile.get("location_city") or "").strip()
        state = str(profile.get("location_state") or "").strip().upper()
        city, state = normalize_location(city, state)
        return {
            "organization": str(profile.get("organization") or ""),
            "title": str(profile.get("title") or ""),
            "description": str(profile.get("description") or ""),
            "priority_area": str(profile.get("priority_area") or ""),
            "location_city": city,
            "location_state": state,
            "org_type": str(profile.get("org_type") or ""),
            "budget_requested": str(profile.get("budget_requested") or ""),
            "eligibility_notes": str(profile.get("eligibility_notes") or ""),
        }

    city, state = location_from_profile(profile)
    return {
        "organization": getattr(profile, "organization", "") or "",
        "title": getattr(profile, "title", "") or "",
        "description": getattr(profile, "description", "") or "",
        "priority_area": getattr(profile, "priority_area", "") or "",
        "location_city": city,
        "location_state": state,
        "org_type": getattr(profile, "org_type", "") or "",
        "budget_requested": str(getattr(profile, "budget_requested", "") or ""),
        "eligibility_notes": getattr(profile, "eligibility_notes", "") or "",
    }


# State codes that are also common English words ("grants in NY" must not read
# the word "in" as Indiana). These only count when written in uppercase.
_WORD_LIKE_STATE_ABBRS = frozenset({"IN", "OR", "ME", "OK", "HI", "LA", "DE"})


def _parse_state(text: str) -> str:
    lowered = f" {text.lower()} "
    # Full state names first (longer matches like "new york").
    for abbr, name in sorted(US_STATE_NAMES.items(), key=lambda x: -len(x[1])):
        if f" {name.lower()} " in lowered or f" in {name.lower()}" in lowered:
            return abbr
    # Uppercase codes as typed: "in CA", ", TX", standalone "NY".
    for abbr in US_STATE_NAMES:
        if re.search(rf"\b{abbr}\b", text):
            return abbr
    # Lowercase codes ("grants in ny"), minus the word-like ones.
    for abbr in US_STATE_NAMES:
        if abbr in _WORD_LIKE_STATE_ABBRS:
            continue
        if re.search(rf"\b{abbr}\b", text, re.I):
            return abbr
    return ""


def _parse_city(text: str) -> tuple[str, str]:
    lowered = text.lower()
    for city, state in sorted(CITY_STATE_HINTS.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{re.escape(city)}\b", lowered):
            # Prefer proper casing of the known city key.
            return city.title() if city not in {"nyc"} else "New York", state
    return "", ""


def _parse_priority(text: str) -> str:
    lowered = text.lower()
    for area in sorted(PRIORITY_AREAS, key=len, reverse=True):
        if area.lower() in lowered:
            return area
    # Soft aliases
    aliases = {
        "mental health": "Health",
        "healthcare": "Health",
        "health care": "Health",
        "affordable housing": "Housing",
        "workforce": "Workforce Development",
        "job training": "Workforce Development",
        "youth": "Youth Development",
        "economic": "Economic Development",
        "downtown": "Downtown Development",
        "food": "Food Access",
        "school": "Education",
        "education": "Education",
        "arts": "Arts",
        "literacy": "Literacy",
        "recreation": "Recreation",
        "public safety": "Public Safety",
        "human services": "Human Services",
        "community": "Community Development",
    }
    for needle, area in aliases.items():
        if needle in lowered:
            return area
    return ""


def _parse_org_type(text: str) -> str:
    lowered = text.lower()
    for alias, value in ORG_TYPE_ALIASES.items():
        if alias in lowered:
            return value
    return ""


def _parse_budget(text: str) -> str:
    # $150,000 / 150000 / 150k / greater than 700000 / budget over 50k
    m = re.search(
        r"(?:budget|need|request(?:ing)?|looking for|under|over|around|upto|up to|"
        r"greater than|more than|at least|above|below)?\s*"
        r"\$?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|m|million|thousand)?",
        text,
        re.I,
    )
    if not m:
        return ""
    raw = m.group(1).replace(",", "")
    try:
        amount = float(raw)
    except ValueError:
        return ""
    suffix = (m.group(2) or "").lower()
    if suffix in {"k", "thousand"}:
        amount *= 1_000
    elif suffix in {"m", "million"}:
        amount *= 1_000_000
    if amount <= 0:
        return ""
    return str(int(amount)) if amount >= 1 else str(amount)


def _parse_keyword(text: str, overrides: dict[str, Any]) -> str:
    """Topic phrase from the user message when they specify one."""
    cleaned = text.strip()
    if not cleaned:
        return ""

    # Prefer explicit topic clauses.
    m = re.search(
        r"\b(?:related to|regarding|about|for)\s+(?:the\s+)?(.+)$",
        cleaned,
        re.I,
    )
    candidate = m.group(1).strip() if m else cleaned

    # Strip location / boilerplate words so "find grants in california" → empty.
    candidate = _BOILERPLATE_RE.sub(" ", candidate)
    if overrides.get("location_state"):
        name = US_STATE_NAMES.get(overrides["location_state"], "")
        if name:
            candidate = re.sub(rf"\b{re.escape(name)}\b", " ", candidate, flags=re.I)
        candidate = re.sub(
            rf"\b{re.escape(overrides['location_state'])}\b", " ", candidate, flags=re.I
        )
    if overrides.get("location_city"):
        candidate = re.sub(
            rf"\b{re.escape(str(overrides['location_city']))}\b",
            " ",
            candidate,
            flags=re.I,
        )
    if overrides.get("priority_area"):
        candidate = re.sub(
            rf"\b{re.escape(str(overrides['priority_area']))}\b",
            " ",
            candidate,
            flags=re.I,
        )
    if overrides.get("budget_requested"):
        candidate = re.sub(
            r"\$?\d[\d,]*(?:\.\d+)?\s*(?:k|m|million|thousand)?",
            " ",
            candidate,
            flags=re.I,
        )

    candidate = re.sub(r"\s+", " ", candidate).strip(" .,!?:;")
    tokens = [t for t in candidate.split() if t.lower() not in _KEYWORD_STOPWORDS]
    candidate = " ".join(tokens).strip()
    # Ignore leftovers that are just noise.
    if len(candidate) < 3:
        return ""
    if candidate.lower() in {"yes", "yeah", "yep", "ok", "okay", "sure", "go ahead"}:
        return ""
    # If priority already captured the topic (e.g. education), don't keep junk.
    if overrides.get("priority_area") and candidate.lower() in {
        str(overrides["priority_area"]).lower()
    }:
        return ""
    return candidate[:180]


def parse_user_overrides(user_query: str) -> dict[str, Any]:
    text = (user_query or "").strip()
    if not text:
        return {}

    overrides: dict[str, Any] = {}
    state = _parse_state(text)
    city, city_state = _parse_city(text)
    if state:
        overrides["location_state"] = state
    if city:
        # "in New York" means the state — don't also set city=New York.
        state_name = US_STATE_NAMES.get(state or city_state or "", "").lower()
        if city.lower() != state_name:
            overrides["location_city"] = city
        if not state and city_state:
            overrides["location_state"] = city_state

    priority = _parse_priority(text)
    if priority:
        overrides["priority_area"] = priority

    org_type = _parse_org_type(text)
    if org_type:
        overrides["org_type"] = org_type

    budget = _parse_budget(text)
    if budget:
        overrides["budget_requested"] = budget

    keyword = _parse_keyword(text, overrides)
    if keyword:
        overrides["keyword"] = keyword
        # Use the stated topic for scoring title when user gave one.
        overrides["title"] = keyword

    return overrides


def resolve_search_context(
    profile: Any,
    user_query: str = "",
) -> dict[str, Any]:
    """
    Profile fields are defaults. User query overrides win for this request only.
    """
    defaults = profile_defaults(profile)
    overrides = parse_user_overrides(user_query)

    effective = dict(defaults)
    applied: dict[str, Any] = {}
    for key, value in overrides.items():
        if value in (None, ""):
            continue
        if key == "keyword":
            continue
        effective[key] = value
        applied[key] = value

    # State override without a city override must not keep the old profile city
    # (Austin + NY is wrong — clear city so tools/scoring use NY only).
    if "location_state" in applied and "location_city" not in applied:
        effective["location_city"] = ""

    city, state = normalize_location(
        effective.get("location_city") or "",
        effective.get("location_state") or "",
    )
    effective["location_city"] = city
    effective["location_state"] = state

    # If query overrode topic fields, don't keep a conflicting profile title.
    if "priority_area" in applied or overrides.get("keyword"):
        if overrides.get("keyword"):
            effective["title"] = str(overrides["keyword"])
        elif "title" not in applied:
            effective["title"] = str(applied.get("priority_area") or effective.get("priority_area") or "")
        # Drop profile description that may mention the old location/topic.
        if user_query and applied:
            effective["description"] = (
                str(overrides.get("keyword") or "")
                or str(effective.get("priority_area") or "")
                or str(user_query)
            )

    keyword = (
        str(overrides.get("keyword") or "").strip()
        or str(effective.get("priority_area") or "").strip()
        or str(effective.get("title") or "").strip()
        or " ".join(str(effective.get("description") or "").split()[:8]).strip()
        or "grant"
    )
    effective["keyword"] = keyword
    effective["user_query"] = (user_query or "").strip()
    effective["overrides"] = applied
    if overrides.get("keyword"):
        effective["overrides"] = {**applied, "keyword": overrides["keyword"]}

    return effective


def scoring_criteria(context: dict[str, Any]) -> dict[str, Any]:
    """
    Criteria used ONLY for scoring/ranking.
    Uses the effective search context (profile defaults + query overrides).
    User-query overrides always win — never mix in conflicting profile leftovers.
    """
    overrides = dict(context.get("overrides") or {})
    user_query = str(context.get("user_query") or "").strip()
    keyword = str(context.get("keyword") or "").strip()
    title = str(context.get("title") or "").strip()

    # If the user stated overrides, score from that intent only —
    # do not let an older profile description (e.g. Texas project) conflict.
    if user_query and overrides:
        description = (
            keyword
            or title
            or str(context.get("priority_area") or "")
            or user_query
        )
        eligibility = ""
    else:
        description = str(context.get("description") or "")[:500] or title or keyword
        eligibility = str(context.get("eligibility_notes") or "")

    return {
        "user_query": user_query,
        "keyword": keyword,
        "title": title or keyword,
        "description": description,
        "priority_area": str(context.get("priority_area") or ""),
        "location_city": str(context.get("location_city") or ""),
        "location_state": str(context.get("location_state") or ""),
        "budget_requested": str(context.get("budget_requested") or ""),
        "org_type": str(context.get("org_type") or ""),
        "eligibility_notes": eligibility,
        "overrides": overrides,
    }


def apply_tool_defaults(
    *,
    keyword: str = "",
    priority_area: str = "",
    location_city: str = "",
    location_state: str = "",
    org_type: str = "",
    defaults: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Fill blank tool args from profile/context defaults."""
    defaults = defaults or {}
    city, state = normalize_location(
        (location_city or defaults.get("location_city") or "").strip(),
        (location_state or defaults.get("location_state") or "").strip(),
    )
    return {
        "keyword": (keyword or defaults.get("keyword") or defaults.get("title") or "grant").strip(),
        "priority_area": (priority_area or defaults.get("priority_area") or "").strip(),
        "location_city": city,
        "location_state": state,
        "org_type": (org_type or defaults.get("org_type") or "").strip(),
    }
