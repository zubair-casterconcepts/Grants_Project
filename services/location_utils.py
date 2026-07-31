"""Normalize user location for Grants.gov / USASpending filters."""

from __future__ import annotations

from typing import Any

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

# Common city → state when user typed an invalid state code (e.g. YT).
CITY_STATE_HINTS = {
    "new york": "NY",
    "nyc": "NY",
    "brooklyn": "NY",
    "queens": "NY",
    "manhattan": "NY",
    "los angeles": "CA",
    "san francisco": "CA",
    "chicago": "IL",
    "houston": "TX",
    "dallas": "TX",
    "austin": "TX",
    "miami": "FL",
    "seattle": "WA",
    "boston": "MA",
    "philadelphia": "PA",
    "atlanta": "GA",
    "denver": "CO",
    "phoenix": "AZ",
}


def normalize_location(city: str = "", state: str = "") -> tuple[str, str]:
    """
    Return (city, USPS state code).

    Fixes invalid codes like YT when city clearly implies a US state.
    """
    city_clean = (city or "").strip()
    state_clean = (state or "").strip().upper()

    if state_clean in US_STATE_NAMES:
        return city_clean, state_clean

    # User typed full state name instead of abbreviation.
    for abbr, name in US_STATE_NAMES.items():
        if state_clean == name.upper():
            return city_clean, abbr

    hint = CITY_STATE_HINTS.get(city_clean.lower())
    if hint:
        return city_clean, hint

    # City string may itself be a state name.
    for abbr, name in US_STATE_NAMES.items():
        if city_clean.lower() == name.lower():
            return city_clean, abbr

    return city_clean, state_clean if state_clean in US_STATE_NAMES else ""


def location_from_profile(profile: Any) -> tuple[str, str]:
    return normalize_location(
        getattr(profile, "location_city", "") or "",
        getattr(profile, "location_state", "") or "",
    )
