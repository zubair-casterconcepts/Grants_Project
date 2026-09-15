"""
Merge saved profile defaults with per-message user overrides for tool calls.

Profile values are defaults. Anything the user states in the current query
(e.g. "find grants in California") overrides only those fields for this search.
"""

from __future__ import annotations

import difflib
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

# Upper bound on terms handed to a keyword search API.
_KEYWORD_MAX_TERMS = 8

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


# Two-letter codes that are also everyday words or abbreviations: "oh ok",
# "my co-op", "wi-fi", "Ms. Johnson", "an MD", "VA benefits", "hi", "or", "me".
# They only count as a state right after a place word ("grants in oh") or after
# a city and a comma ("Portland, OR").
_AMBIGUOUS_STATE_CODES = frozenset(
    {
        "AL", "CO", "DE", "HI", "ID", "IN", "LA", "MA", "MD", "ME",
        "MS", "MT", "NE", "OH", "OK", "OR", "PA", "VA", "WI",
    }
)
_PLACE_WORD_BEFORE_RE = re.compile(
    r"\b(?:in|near|around|across|within|throughout|from)\s+$", re.I
)
_CITY_COMMA_BEFORE_RE = re.compile(r"[A-Za-z],\s*$")
# A place named after one of these is excluded, not searched ("not in texas").
_NEGATED_BEFORE_RE = re.compile(
    r"\b(?:not\s+(?:in\s+|from\s+)?|outside\s+(?:of\s+)?|except\s+(?:for\s+)?|"
    r"excluding\s+|other\s+than\s+)$",
    re.I,
)
_DC_RE = re.compile(
    r"\b(?:washington,?\s+d\.?\s?c\b\.?|district\s+of\s+columbia\b)", re.I
)
# A standalone two-letter token: not part of "co-op", "wi-fi", "it's" or "Ms.J".
_STATE_CODE_TOKEN_RE = re.compile(r"(?<![\w.'-])([A-Za-z]{2})(?![\w'-])(?!\.[A-Za-z])")


def _parse_state(text: str) -> str:
    """
    The US state a message is about, or "" when none is named.

    Checked in order: Washington DC, full state names, then two-letter codes.
    A place after "not in", "outside" or "except" is skipped, not searched.
    """
    body = text or ""
    lowered = body.lower()

    def negated(start: int) -> bool:
        return bool(_NEGATED_BEFORE_RE.search(lowered[:start]))

    dc = _DC_RE.search(body)
    if dc and not negated(dc.start()):
        return "DC"

    # Longest names first, so "west virginia" wins over "virginia".
    for abbr, name in sorted(US_STATE_NAMES.items(), key=lambda item: -len(item[1])):
        for match in re.finditer(rf"\b{re.escape(name.lower())}\b", lowered):
            if not negated(match.start()):
                return abbr

    # In an all-caps message capitals carry no meaning ("HI, FIND GRANTS IN CA").
    letters = [ch for ch in body if ch.isalpha()]
    shouting = bool(letters) and sum(ch.isupper() for ch in letters) > 0.8 * len(letters)

    for match in _STATE_CODE_TOKEN_RE.finditer(body):
        code = match.group(1)
        abbr = code.upper()
        # "LA" almost always means Los Angeles; Louisiana gets written out.
        if abbr not in US_STATE_NAMES or abbr == "LA" or negated(match.start()):
            continue
        if abbr not in _AMBIGUOUS_STATE_CODES:
            return abbr
        before = body[: match.start()]
        capitals = code.isupper() and not shouting
        if _PLACE_WORD_BEFORE_RE.search(before) or (
            capitals and _CITY_COMMA_BEFORE_RE.search(before)
        ):
            return abbr
    return ""


def _parse_city(text: str) -> tuple[str, str]:
    # "grants in LA" / "L.A. youth programs" mean Los Angeles.
    if re.search(r"(?<![\w.])L\.?A\.?(?!\w)", text or ""):
        return "Los Angeles", "CA"
    lowered = (text or "").lower()
    for city, state in sorted(CITY_STATE_HINTS.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{re.escape(city)}\b", lowered):
            # Prefer proper casing of the known city key.
            return city.title() if city not in {"nyc"} else "New York", state
    return "", ""


# Soft aliases → priority area.
_PRIORITY_ALIASES = {
    "mental health": "Health",
    "healthcare": "Health",
    "health care": "Health",
    "medical": "Health",
    "clinic": "Health",
    "addiction": "Health",
    "substance abuse": "Health",
    "affordable housing": "Housing",
    "homeless": "Housing",
    "homelessness": "Housing",
    "workforce": "Workforce Development",
    "job training": "Workforce Development",
    "jobs": "Workforce Development",
    "employment": "Workforce Development",
    "apprenticeship": "Workforce Development",
    "youth": "Youth Development",
    "mentorship": "Youth Development",
    "mentoring": "Youth Development",
    "after school": "Youth Development",
    "after-school": "Youth Development",
    "afterschool": "Youth Development",
    "economic": "Economic Development",
    "small business": "Economic Development",
    "entrepreneur": "Economic Development",
    "entrepreneurs": "Economic Development",
    "downtown": "Downtown Development",
    "food": "Food Access",
    "hunger": "Food Access",
    "nutrition": "Food Access",
    "meals": "Food Access",
    "school": "Education",
    "education": "Education",
    "stem": "Education",
    "tutoring": "Education",
    "teacher": "Education",
    "teachers": "Education",
    "arts": "Arts",
    "music": "Arts",
    "theater": "Arts",
    "theatre": "Arts",
    "museum": "Culture",
    "museums": "Culture",
    "heritage": "Culture",
    "literacy": "Literacy",
    "reading": "Literacy",
    "library": "Literacy",
    "libraries": "Literacy",
    "books": "Literacy",
    "recreation": "Recreation",
    "sports": "Recreation",
    "parks": "Recreation",
    "playground": "Recreation",
    "public safety": "Public Safety",
    "police": "Public Safety",
    "human services": "Human Services",
    "community": "Community Development",
}

# Umbrella areas lose a tie to a more specific one. A request about "literacy"
# that happens to name the "Department of Education" is a Literacy request.
_UMBRELLA_AREAS = {"Education", "Community Development", "Human Services"}

# Topic words worth auto-correcting. A typo here silently changes the search:
# "eduction" matched no topic, so the search fell back to the saved profile's
# topic and Grants.gov was sent the nonsense keyword "eduction" (0 results).
_TOPIC_VOCABULARY = sorted(
    {
        word
        for phrase in (*PRIORITY_AREAS, *_PRIORITY_ALIASES, "tutoring", "scholarship")
        for word in phrase.lower().split()
        if len(word) >= 5
    }
)
_TOPIC_VOCAB_SET = frozenset(_TOPIC_VOCABULARY)


def _correct_topic_typos(text: str) -> str:
    """
    Fix near-miss spellings of topic words ("eduction" → "education").

    Deliberately strict so real words are never rewritten: same first two
    letters, close length and high similarity — and inflections such as
    "communities", "healthy" or "educational" are left alone.
    """

    def fix(match: re.Match[str]) -> str:
        word = match.group(0)
        lower = word.lower()
        if len(lower) < 5 or lower in _TOPIC_VOCAB_SET:
            return word
        for candidate in difflib.get_close_matches(
            lower, _TOPIC_VOCABULARY, n=3, cutoff=0.9
        ):
            if lower.startswith(candidate[:-1]) or candidate.startswith(lower):
                continue  # inflection or truncation, not a typo
            if candidate[:2] == lower[:2] and abs(len(candidate) - len(lower)) <= 2:
                return candidate
        return word

    return re.sub(r"[A-Za-z]+", fix, text or "")


def priority_terms(area: str) -> set[str]:
    """Words that mean a priority area: its own name plus its aliases."""
    target = (area or "").strip()
    if not target:
        return set()
    words = {word for word in target.lower().split() if len(word) > 3}
    for alias, mapped in _PRIORITY_ALIASES.items():
        # Single-word aliases only: splitting "mental health" or "health care"
        # would turn "mental" and "care" into Health matches on their own.
        if mapped == target and " " not in alias and len(alias) > 3:
            words.add(alias)
    return words


def _parse_priority(text: str) -> str:
    """
    Pick the priority area the text is actually *about*.

    Scored by how often each area is referenced, not by which name happens to be
    longest. Direct mentions of the area itself outweigh loose aliases, and an
    umbrella area (Education) yields to a specific one (Literacy) on a tie.
    """
    lowered = text.lower()
    if not lowered.strip():
        return ""

    scores: dict[str, int] = {}
    direct: set[str] = set()

    # Direct mentions of the area name are worth more than alias hits.
    for area in PRIORITY_AREAS:
        hits = len(re.findall(rf"\b{re.escape(area.lower())}\b", lowered))
        if hits:
            scores[area] = scores.get(area, 0) + hits * 3
            direct.add(area)

    for needle, area in _PRIORITY_ALIASES.items():
        hits = len(re.findall(rf"\b{re.escape(needle)}\b", lowered))
        if hits:
            scores[area] = scores.get(area, 0) + hits

    if not scores:
        return ""

    def _rank(area: str) -> tuple[int, int, int, int]:
        return (
            scores[area],
            1 if area in direct else 0,
            0 if area in _UMBRELLA_AREAS else 1,
            len(area),
        )

    return max(scores, key=_rank)


# Only treat an org type as an override when the user is describing *themselves*.
# Naming a school as the beneficiary ("a literacy program at an elementary
# school") must not reclassify a community foundation as a school — org type
# gates eligibility, so a wrong guess silently hides every valid opportunity.
# "as" only counts with an article ("as a nonprofit"); bare "as" matched
# "programs such as school tutoring" and reclassified the applicant as a school.
_SELF_IDENTIFY_RE = re.compile(
    r"\b(?:we\s+are|we['’]re|our\s+(?:org(?:anization)?|agency|team)\s+is|"
    r"i\s+am|i['’]m|as\s+an?)\s+(?:an?\s+)?(?:[a-z-]+\s+){0,2}?"
    r"(501\(c\)\(3\)|501c3|non[-\s]?profit|nonprofit|government|municipal|"
    r"school\s+district|school|district)\b",
    re.I,
)


# Phrases that name the applicant outright. A community foundation or fiscal
# sponsor is a 501(c)(3) public charity, so saying who is applying is enough —
# no "we are a…" preamble required.
_APPLICANT_ORG_PATTERNS = (
    (re.compile(r"\bcommunity\s+foundation\b", re.I), "501c3"),
    (re.compile(r"\bfiscal\s+sponsor(?:ship)?\b", re.I), "501c3"),
    (re.compile(r"\bgrant\s+intermediar(?:y|ies)\b", re.I), "501c3"),
    (re.compile(r"\b501\s*\(?c\)?\s*\(?3\)?\b", re.I), "501c3"),
    # "grants for nonprofits" / "for local governments" / "for school districts"
    # name the applicant too; without these a nonprofit's search was screened
    # against the saved profile's org type instead.
    (re.compile(r"\bfor\s+(?:an?\s+|our\s+)?non[-\s]?profits?\b", re.I), "501c3"),
    (
        re.compile(
            r"\bfor\s+(?:our\s+)?(?:local|city|county|municipal|tribal)?\s*governments?\b", re.I
        ),
        "government",
    ),
    (re.compile(r"\bfor\s+(?:our\s+)?(?:public\s+)?school\s+districts?\b", re.I), "school"),
)


def _parse_org_type(text: str) -> str:
    body = text or ""
    for pattern, value in _APPLICANT_ORG_PATTERNS:
        if pattern.search(body):
            return value

    match = _SELF_IDENTIFY_RE.search(body)
    if not match:
        return ""
    stated = match.group(1).lower().replace("  ", " ")
    for alias, value in ORG_TYPE_ALIASES.items():
        if alias in stated:
            return value
    if "school" in stated or "district" in stated:
        return "school"
    if "government" in stated or "municipal" in stated:
        return "government"
    return ""


_MONEY_SUFFIX = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "million": 1_000_000}
_AMOUNT = r"(\$)?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|m|million|thousand)?\b"
_BUDGET_RANGE_RE = re.compile(
    rf"(?:between\s+)?{_AMOUNT}\s*(?:to|-|–|—|and|through)\s*{_AMOUNT}", re.I
)


def _to_amount(digits: str | None, suffix: str | None) -> float | None:
    try:
        value = float((digits or "").replace(",", ""))
    except ValueError:
        return None
    return value * _MONEY_SUFFIX.get((suffix or "").lower(), 1)


def _looks_like_money(dollar: str | None, digits: str, suffix: str | None, value: float) -> bool:
    if dollar or suffix or "," in digits:
        return True
    if 1900 <= value <= 2100:  # a year, not a budget
        return False
    return value >= 10_000


def _parse_budget_range(text: str) -> tuple[str, str]:
    """
    A funding range: "50000 to 300000", "$50k-$300k", "between 50,000 and 300,000".

    Returns ("", "") when there is no believable range — years ("2025-2026") and
    grade spans ("K-6", "3-5") are not budgets.
    """
    for match in _BUDGET_RANGE_RE.finditer(text or ""):
        d1, n1, s1, d2, n2, s2 = match.groups()
        low, high = _to_amount(n1, s1), _to_amount(n2, s2)
        if low is None or high is None:
            continue
        # "50-300k": the suffix on the second number applies to the first.
        if s2 and not s1 and low < 1_000:
            low *= _MONEY_SUFFIX.get(s2.lower(), 1)
            s1 = s2
        if not (_looks_like_money(d1, n1, s1, low) or _looks_like_money(d2, n2, s2, high)):
            continue
        low, high = sorted((low, high))
        if low < 500 or high <= low:
            continue
        return str(int(low)), str(int(high))
    return "", ""


def _parse_budget(text: str) -> str:
    """
    Pull a single funding amount out of the message.

    Requires a real money signal — a `$`, a thousands separator, a k/m suffix,
    or a budget word shortly before the number ("budget is around 50000").
    Without that, "K-6 literacy", "21st Century" or "fiscal year 2026" would
    read as a budget.
    """
    patterns = (
        r"(\$)\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|m|million|thousand)?\b",
        r"()\b(\d{1,3}(?:,\d{3})+)\s*(k|m|million|thousand)?\b",
        r"()\b(\d+(?:\.\d+)?)\s*(k|m|million|thousand)\b",
        r"(?:budget|need|request(?:ing)?|looking\s+for|award|funding|grant\s+of|"
        r"under|over|around|arround|about|approximately|roughly|upto|up\s+to|"
        r"greater\s+than|more\s+than|at\s+least|above|below)"
        r"(?:\s+[a-z]+){0,3}?\s*()\$?\s*(\d{4,}(?:\.\d+)?)\s*(k|m|million|thousand)?\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text or "", re.I):
            dollar, digits, suffix = match.group(1), match.group(2), match.group(3)
            value = _to_amount(digits, suffix)
            if value is None or value < 500:
                continue
            if not (dollar or suffix or "," in digits) and 1900 <= value <= 2100:
                continue
            return str(int(value))
    return ""


# Words that describe the request rather than its subject. Left in, they become
# the search keyword ("show me some grants" searched for "some", "thanks!" for
# "thanks") and they match almost any grant text in the topic check.
_KEYWORD_FILLER = frozenset(
    """
    hi hello hey thanks thank please pls ok okay oh yes yeah yep sure
    what which who whom how when where why can could would should will shall
    do does did is are am was were be been being it its i i'm im we we're
    our ours us my mine me you your they their them this that these those
    some any all more most other others new best top good great both each
    such etc not no without except excluding outside
    available current currently open also maybe may might just really very
    help give list need needs needed want wants looking look like likes
    related type types kind kinds sort something anything stuff thing things
    there here now today right get find search show see tell know task
    provide include including based focus focused prioritize avoid
    criteria requirement requirements eligible eligibility
    dollar dollars money budget range between million thousand usd
    grant grants funding funds fund funder funders opportunity opportunities
    apply application program programs project projects
    support supports supporting initiative initiatives
    city state county town area region local nationwide national usa
    government governments nonprofit non-profit nonprofits organization
    organizations org agency agencies foundation foundations
    mr mrs ms dr
    but however though arround dc d.c la l.a
    """.split()
)

# Standalone amounts and years ("50000", "$2M", "1 million", "2026") — but not
# grade spans like "K-8" or ordinals like "21st".
_STANDALONE_AMOUNT_RE = re.compile(
    r"(?<![\w-])\$?\d[\d,]*(?:\.\d+)?(?:\s*(?:million|thousand|k|m))?(?![\w-])",
    re.I,
)


def _parse_keyword(text: str, overrides: dict[str, Any]) -> str:
    """
    The subject words of a request, compressed for a keyword search API.

    Everything that is not the subject is removed: request phrasing, filler,
    places, org types and amounts. What remains is what the grant should be
    about — "STEM education for girls" keeps "STEM girls", not just "girls".
    """
    candidate = (text or "").strip()
    if not candidate:
        return ""

    candidate = _STANDALONE_AMOUNT_RE.sub(" ", candidate)
    candidate = _BOILERPLATE_RE.sub(" ", candidate)

    places = {name.lower() for name in US_STATE_NAMES.values()} | set(CITY_STATE_HINTS)
    places.add("district of columbia")
    if overrides.get("location_city"):
        places.add(str(overrides["location_city"]).lower())
    for place in sorted(places, key=len, reverse=True):
        candidate = re.sub(rf"\b{re.escape(place)}\b", " ", candidate, flags=re.I)
    state = str(overrides.get("location_state") or "")
    if state:
        candidate = re.sub(rf"\b{re.escape(state)}\b", " ", candidate, flags=re.I)

    # Keep multi-word topics whole, so removing the topic name below turns
    # "mental health" into nothing rather than a bare "mental".
    for phrase in sorted((a for a in _PRIORITY_ALIASES if " " in a), key=len, reverse=True):
        candidate = re.sub(
            rf"\b{re.escape(phrase)}\b", phrase.replace(" ", "_"), candidate, flags=re.I
        )

    if overrides.get("priority_area"):
        candidate = re.sub(
            rf"\b{re.escape(str(overrides['priority_area']))}\b",
            " ",
            candidate,
            flags=re.I,
        )

    tokens: list[str] = []
    seen: set[str] = set()
    for raw in candidate.split():
        token = raw.strip(" .,!?:;()[]{}\"'`").replace("_", " ")
        if token.lower().endswith("'s"):
            token = token[:-2]
        word = token.lower()
        if len(word) < 2 or not re.search(r"[a-z]", word):
            continue
        if word in _KEYWORD_STOPWORDS or word in _KEYWORD_FILLER or word in seen:
            continue
        seen.add(word)
        tokens.append(token)
        if len(tokens) >= _KEYWORD_MAX_TERMS:
            break

    keyword = " ".join(tokens).strip()
    if len(keyword) < 3:
        return ""
    if (
        overrides.get("priority_area")
        and keyword.lower() == str(overrides["priority_area"]).lower()
    ):
        return ""
    return keyword[:180]


def parse_user_overrides(user_query: str) -> dict[str, Any]:
    # "Start over", "…with my saved project" and similar ask for the saved project;
    # left in, their words ("saved") would be read as the search topic.
    text = _correct_topic_typos(_SAVED_PROJECT_PHRASE_RE.sub(" ", user_query or "").strip())
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

    budget_low, budget_high = _parse_budget_range(text)
    if budget_low:
        # A range: the low end is the ask, the high end caps what still fits.
        overrides["budget_requested"] = budget_low
        overrides["budget_max"] = budget_high
    else:
        budget = _parse_budget(text)
        if budget:
            overrides["budget_requested"] = budget

    keyword = _parse_keyword(text, overrides)
    if keyword:
        overrides["keyword"] = keyword
        # Use the stated topic for scoring title when user gave one.
        overrides["title"] = keyword
        if not overrides.get("priority_area"):
            # "educational", "schools", "artistic" are not alias words, so the
            # saved profile's focus area used to win and an education search came
            # back full of arts grants. Read the focus area from the subject words
            # the user typed; if they name none, the profile's stays.
            from services.grant_categories import FALLBACK_CATEGORY, normalize_category

            inferred = normalize_category(keyword)
            if inferred and inferred != FALLBACK_CATEGORY:
                overrides["priority_area"] = inferred

    return overrides


# Phrases like "start over" or "search with my saved project" (the "no grants"
# card's button) ask for the saved project; they are not search words.
_SAVED_PROJECT_PHRASE_RE = re.compile(
    r"\b(?:start\s+(?:over|fresh|again)|new\s+search|reset|clear\s+(?:all\s+|the\s+)?filters|"
    r"forget\s+(?:that|this|everything|previous|earlier|the\s+(?:previous|earlier))|"
    r"(?:use|from|with)\s+my\s+(?:saved\s+)?(?:profile|project))\b",
    re.I,
)


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
