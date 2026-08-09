"""
Canonical subject categories for grant matches.

Every source labels subject areas differently: Grants.gov returns funding
activity categories (either full labels or two-letter codes), GrantedAI returns
free-text tags, and USASpending returns nothing at all. Cards need one short
label per grant, so this module folds all of that onto a single vocabulary and
falls back to keyword inference when a source gives us nothing to work with.
"""

from __future__ import annotations

import re
from typing import Any

FALLBACK_CATEGORY = "General"

CATEGORY_CHOICES: tuple[str, ...] = (
    "Agriculture",
    "Arts",
    "Community Development",
    "Culture",
    "Disaster Relief",
    "Economic Development",
    "Education",
    "Energy",
    "Environment",
    "Food Access",
    "Health",
    "Housing",
    "Human Services",
    "Infrastructure",
    "Literacy",
    "Public Safety",
    "Recreation",
    "Science & Technology",
    "Transportation",
    "Veterans",
    "Workforce Development",
    "Youth Development",
    FALLBACK_CATEGORY,
)

# Exact labels/codes we already know from the sources and the profile intake.
# Keys are normalized with _normalize_text() before lookup.
_ALIASES: dict[str, str] = {
    # Grants.gov funding activity category codes
    "ag": "Agriculture",
    "ar": "Arts",
    "bc": "Economic Development",
    "cd": "Community Development",
    "cp": "Human Services",
    "dpr": "Disaster Relief",
    "ed": "Education",
    "elt": "Workforce Development",
    "en": "Energy",
    "env": "Environment",
    "fn": "Food Access",
    "hl": "Health",
    "ho": "Housing",
    "hu": "Culture",
    "iij": "Infrastructure",
    "iss": "Human Services",
    "ljl": "Public Safety",
    "nr": "Environment",
    "rd": "Community Development",
    "st": "Science & Technology",
    "t": "Transportation",
    "o": FALLBACK_CATEGORY,
    # Grants.gov funding activity category labels
    "agriculture": "Agriculture",
    "arts": "Arts",
    "arts see cultural affairs in cfda": "Arts",
    "business and commerce": "Economic Development",
    "community development": "Community Development",
    "consumer protection": "Human Services",
    "disaster prevention and relief": "Disaster Relief",
    "education": "Education",
    "employment labor and training": "Workforce Development",
    "energy": "Energy",
    "environment": "Environment",
    "environmental quality": "Environment",
    "food and nutrition": "Food Access",
    "health": "Health",
    "housing": "Housing",
    "humanities": "Culture",
    "humanities see cultural affairs in cfda": "Culture",
    "income security and social services": "Human Services",
    "information and statistics": "Science & Technology",
    "infrastructure investment and jobs act": "Infrastructure",
    "law justice and legal services": "Public Safety",
    "natural resources": "Environment",
    "regional development": "Community Development",
    "science and technology and other research and development": "Science & Technology",
    "transportation": "Transportation",
    "opportunity zone benefits": "Economic Development",
    "recovery act": "Economic Development",
    "other": FALLBACK_CATEGORY,
    # Profile priority areas that are not already exact category names
    "culture": "Culture",
    "downtown development": "Community Development",
    "economic development": "Economic Development",
    "food access": "Food Access",
    "human services": "Human Services",
    "literacy": "Literacy",
    "public safety": "Public Safety",
    "recreation": "Recreation",
    "workforce development": "Workforce Development",
    "youth development": "Youth Development",
    # Common free-text tags from GrantedAI
    "arts and culture": "Arts",
    "public health": "Health",
    "mental health": "Health",
    "affordable housing": "Housing",
    "small business": "Economic Development",
    "stem": "Science & Technology",
    "technology": "Science & Technology",
    "research": "Science & Technology",
    "climate": "Environment",
    "conservation": "Environment",
    "veterans": "Veterans",
    "parks and recreation": "Recreation",
    "workforce": "Workforce Development",
    "youth": "Youth Development",
    "general": FALLBACK_CATEGORY,
}

# Ordered most-specific first: ties during inference go to the earlier entry.
# Terms are matched as word prefixes, so "agricultur" also covers "agricultural".
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Veterans", ("veteran", "military", "service member", "servicemember")),
    (
        "Disaster Relief",
        (
            "disaster",
            "hazard mitigation",
            "flood",
            "hurricane",
            "wildfire",
            "emergency management",
            "resilienc",
        ),
    ),
    ("Literacy", ("literacy", "reading program", "librar", "book")),
    (
        "Youth Development",
        ("youth", "after school", "afterschool", "mentor", "juvenile", "teen"),
    ),
    (
        "Education",
        (
            "education",
            "school",
            "student",
            "teacher",
            "classroom",
            "curricul",
            "tutor",
            "early learning",
            "scholarship",
            "university",
            "college",
        ),
    ),
    (
        "Health",
        (
            "health",
            "medical",
            "medicaid",
            "medicare",
            "clinic",
            "hospital",
            "disease",
            "substance use",
            "opioid",
            "nursing",
            "wellness",
            "behavioral",
            "maternal",
        ),
    ),
    (
        "Housing",
        ("housing", "homeless", "shelter", "rental", "home repair", "tenant"),
    ),
    ("Food Access", ("food", "nutrition", "hunger", "meal", "pantry", "snap benefit")),
    (
        "Human Services",
        (
            "human service",
            "social service",
            "income security",
            "child care",
            "childcare",
            "senior",
            "aging",
            "disabilit",
            "poverty",
            "welfare",
            "family support",
        ),
    ),
    (
        "Arts",
        (
            "art",
            "artist",
            "music",
            "theater",
            "theatre",
            "dance",
            "museum",
            "gallery",
            "mural",
            "film",
            "creative",
        ),
    ),
    ("Culture", ("cultur", "heritage", "historic", "preservation", "humanities", "folk")),
    (
        "Recreation",
        ("recreation", "park", "trail", "playground", "sport", "athletic", "outdoor"),
    ),
    (
        "Public Safety",
        (
            "public safety",
            "police",
            "law enforcement",
            "justice",
            "crime",
            "violence",
            "fire department",
            "legal service",
            "correction",
        ),
    ),
    (
        "Workforce Development",
        (
            "workforce",
            "job training",
            "apprenticeship",
            "employment",
            "career",
            "reemploy",
            "upskill",
            "labor",
        ),
    ),
    (
        "Economic Development",
        (
            "economic development",
            "small business",
            "entrepreneur",
            "commerce",
            "tourism",
            "job creation",
            "downtown",
            "main street",
            "industr",
        ),
    ),
    (
        "Community Development",
        (
            "community development",
            "neighborhood",
            "revitaliz",
            "civic",
            "community facilit",
            "placemaking",
            "rural development",
        ),
    ),
    (
        "Transportation",
        (
            "transportation",
            "transit",
            "highway",
            "roadway",
            "bridge",
            "bicycle",
            "pedestrian",
            "airport",
            "railway",
        ),
    ),
    (
        "Infrastructure",
        (
            "infrastructure",
            "broadband",
            "sewer",
            "wastewater",
            "water system",
            "drinking water",
            "utilit",
        ),
    ),
    (
        "Energy",
        ("energy", "solar", "renewable", "electric grid", "power plant", "biofuel"),
    ),
    (
        "Environment",
        (
            "environment",
            "conservation",
            "climate",
            "pollution",
            "wildlife",
            "habitat",
            "natural resource",
            "recycl",
            "brownfield",
            "forest",
            "watershed",
        ),
    ),
    (
        "Agriculture",
        ("agricultur", "farm", "ranch", "crop", "livestock", "horticultur"),
    ),
    (
        "Science & Technology",
        (
            "research",
            "technolog",
            "innovation",
            "engineering",
            "scientif",
            "laboratory",
            "cyber",
            "artificial intelligence",
            "data science",
        ),
    ),
)

# Weighted per-field text used when a grant carries no usable category labels.
_INFERENCE_FIELDS: tuple[tuple[str, int], ...] = (
    ("title", 3),
    ("funding_categories", 3),
    ("alns", 1),
    ("eligibility", 1),
    ("description", 1),
    ("agency", 1),
    ("top_agency", 1),
)

_SPLIT_RE = re.compile(r"[,;|]")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def _normalize_text(value: Any) -> str:
    return _NON_WORD_RE.sub(" ", str(value or "").lower()).strip()


def _keyword_hits(text: str) -> dict[str, int]:
    """Count word-prefix keyword matches per category in normalized text."""
    hits: dict[str, int] = {}
    for label, terms in _KEYWORDS:
        count = 0
        for term in terms:
            if re.search(rf"\b{re.escape(term)}", text):
                count += 1
        if count:
            hits[label] = count
    return hits


def _best_keyword_match(text: str) -> str:
    hits = _keyword_hits(text)
    if not hits:
        return ""
    order = {label: i for i, (label, _) in enumerate(_KEYWORDS)}
    return max(hits.items(), key=lambda kv: (kv[1], -order[kv[0]]))[0]


def normalize_category(value: Any) -> str:
    """Map a single source label/code onto a canonical category, or "" if unknown."""
    key = _normalize_text(value)
    if not key:
        return ""
    if key in _ALIASES:
        return _ALIASES[key]
    for choice in CATEGORY_CHOICES:
        if key == _normalize_text(choice):
            return choice
    return _best_keyword_match(key)


def derive_category(row: dict[str, Any]) -> str:
    """Best-effort category for a grant row; never returns an empty string."""
    for part in _SPLIT_RE.split(str(row.get("funding_categories") or "")):
        label = normalize_category(part)
        if label and label != FALLBACK_CATEGORY:
            return label

    weighted: dict[str, int] = {}
    for field, weight in _INFERENCE_FIELDS:
        text = _normalize_text(row.get(field))
        if not text:
            continue
        for label, count in _keyword_hits(text).items():
            weighted[label] = weighted.get(label, 0) + count * weight
    if not weighted:
        return FALLBACK_CATEGORY

    order = {label: i for i, (label, _) in enumerate(_KEYWORDS)}
    return max(weighted.items(), key=lambda kv: (kv[1], -order[kv[0]]))[0]
