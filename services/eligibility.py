"""
Strict eligibility gating for grant recommendations.

Feedback from grant writers: search tools surface too many "long shots" that
turn out to be ineligible after a little digging, and too many opportunities
from far-flung areas. Scoring alone never fixes that — a low score still shows
the card. So this module answers a hard yes/no *before* an opportunity is
recommended, and records the reason so it can be shown or logged.

Three gates, cheapest first:

1. Applicant type — does the funder's applicant list actually include this
   org type? (Grants.gov exposes structured `applicantTypes`.)
2. Geography     — is the opportunity locked to a different state?
3. Record type   — is this an applicable opportunity at all, or a historical
   award already paid out to someone else?

Anything we cannot judge is reported as `unverified` rather than guessed at, so
callers decide whether to keep or drop it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from services.location_utils import CITY_STATE_HINTS, US_STATE_NAMES

ELIGIBLE = "eligible"
INELIGIBLE = "ineligible"
UNVERIFIED = "unverified"

# Grants.gov applicant-type labels that mean "open to essentially anyone".
_OPEN_TO_ALL_MARKERS = (
    "unrestricted",
    "any type of entity",
    "others (see text field",
    "other (see text field",
)

# Applicant classes per profile org type. They must read both Grants.gov's
# structured labels ("County governments") and the free text other sources use
# ("government entities", "public bodies") — matching only the Grants.gov labels
# rejected government applicants for grants that explicitly named them.
_ORG_TYPE_APPLICANT_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "501c3": (
        re.compile(r"\bnon[-\s]?profits?\b"),
        re.compile(r"\b501\s*\(?c\)?\s*\(?3\)?"),
        re.compile(r"\bnot[-\s]for[-\s]profit\b"),
        re.compile(r"\bcharit(?:y|ies|able)\b"),
        re.compile(r"\btax[-\s]exempt\b"),
        re.compile(r"\bnon[-\s]?governmental\b"),
        re.compile(r"\bcommunity[-\s]based\s+organizations?\b"),
        re.compile(r"\bcommunity\s+foundations?\b"),
        re.compile(r"\bfaith[-\s]based\b"),
    ),
    "government": (
        # "government entities" / "state governments", never "non-governmental".
        re.compile(r"(?<!non-)(?<!non)\bgovernment(?:s|al)?\b"),
        re.compile(
            r"\bpublic\s+(?:bod(?:y|ies)|agenc(?:y|ies)|entit(?:y|ies)|authorit(?:y|ies))\b"
        ),
        re.compile(r"\bmunicipal(?:ity|ities)?\b"),
        re.compile(r"\b(?:counties|cities|townships)\b"),
        re.compile(r"\bspecial\s+districts?\b"),
        re.compile(r"\btrib(?:e|es|al)\b"),
        re.compile(r"\bhousing\s+authorit(?:y|ies)\b"),
        re.compile(r"\bstate\s+agenc(?:y|ies)\b"),
    ),
    "school": (
        re.compile(r"\bschool\s+districts?\b"),
        re.compile(r"\b(?:public|private|charter|elementary|secondary|k-12)\s+schools?\b"),
        re.compile(r"\binstitutions?\s+of\s+higher\s+education\b"),
        re.compile(r"\beducational\s+institutions?\b"),
        re.compile(r"\blocal\s+educational?\s+agenc(?:y|ies)\b"),
        re.compile(r"\b(?:universit(?:y|ies)|colleges?)\b"),
        re.compile(r"\bcounty\s+offices?\s+of\s+education\b"),
    ),
}

# Applicant classes outside the profile org types. Seeing one still proves the
# funder published a real applicant list (so a mismatch is a genuine "no").
_OTHER_APPLICANT_PATTERNS = (
    re.compile(
        r"\b(?:for[-\s]profits?|businesses|business\s+entit(?:y|ies)|"
        r"small\s+business(?:es)?|companies|corporations?)\b"
    ),
    re.compile(r"\bindividuals?\b"),
)


def _names_applicant_classes(text: str) -> bool:
    return any(
        pattern.search(text)
        for patterns in (*_ORG_TYPE_APPLICANT_PATTERNS.values(), _OTHER_APPLICANT_PATTERNS)
        for pattern in patterns
    )


# A 501(c)(3) is NOT eligible when the funder explicitly wants non-501(c)(3)
# nonprofits only. Detect that phrasing so the generic "nonprofit" match above
# does not wave it through.
_NON_501C3_ONLY = re.compile(r"do(?:es)?\s+not\s+have\s+a?\s*501\s*\(?c\)?\s*\(?3\)?", re.I)
_HAS_501C3 = re.compile(r"having\s+a?\s*501\s*\(?c\)?\s*\(?3\)?", re.I)

# Wording that signals a nationwide / federal program (never geo-restricted).
_NATIONAL_MARKERS = (
    "nationwide",
    "national",
    "all states",
    "united states",
    "u.s.",
    "federal",
)

_STATE_NAME_TO_ABBR = {name.lower(): abbr for abbr, name in US_STATE_NAMES.items()}


@dataclass(frozen=True)
class EligibilityVerdict:
    """Outcome of the eligibility gate for one opportunity."""

    status: str
    reasons: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()

    @property
    def is_ineligible(self) -> bool:
        return self.status == INELIGIBLE

    @property
    def is_eligible(self) -> bool:
        return self.status == ELIGIBLE


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


# ── Gate 1: applicant type ──────────────────────────────────────────────────


def check_applicant_type(row: dict[str, Any], org_type: str) -> EligibilityVerdict:
    """Does the funder's applicant list include this organization type?"""
    eligibility = _lower(row.get("eligibility"))
    if not eligibility:
        return EligibilityVerdict(UNVERIFIED, ("applicant types not published",))

    if any(marker in eligibility for marker in _OPEN_TO_ALL_MARKERS):
        return EligibilityVerdict(ELIGIBLE, ("open to all applicant types",))

    org = _lower(org_type)
    patterns = _ORG_TYPE_APPLICANT_PATTERNS.get(org)
    if not patterns:
        # Profile org type is "other"/blank — we cannot judge a real list.
        return EligibilityVerdict(UNVERIFIED, ("org type not specific enough to verify",))

    if org == "501c3" and _NON_501C3_ONLY.search(eligibility) and not _HAS_501C3.search(
        eligibility
    ):
        return EligibilityVerdict(
            INELIGIBLE,
            blockers=("funder excludes 501(c)(3) nonprofits",),
        )

    if any(pattern.search(eligibility) for pattern in patterns):
        return EligibilityVerdict(ELIGIBLE, ("applicant type matches your org",))

    # Only a published applicant list that leaves this org type out is a real
    # "no". Text that names no applicant class at all ("workforce and economic
    # development organizations") cannot be judged, so it must not be dropped.
    if _names_applicant_classes(eligibility):
        return EligibilityVerdict(
            INELIGIBLE,
            blockers=(f"applicants limited to: {_text(row.get('eligibility'))[:110]}",),
        )
    return EligibilityVerdict(
        UNVERIFIED,
        (f"applicant types not clearly stated: {_text(row.get('eligibility'))[:80]}",),
    )


# ── Gate 2: geography ───────────────────────────────────────────────────────


# City names that contain a state's name, and "Washington, D.C.", must not read
# as that state ("Kansas City" is in Missouri).
_CITY_WITH_STATE_WORD = re.compile(
    r"\b(?:kansas|oklahoma|iowa|carson|texas|jersey|salt\s+lake|nevada)\s+city\b|\bvirginia\s+beach\b",
    re.I,
)
_DC_RE = re.compile(r"\bwashington,?\s*d\.?\s*c\b\.?", re.I)


def _state_names_in(text: str) -> set[str]:
    """States written out in full. Longest names first, so "West Virginia" is not also Virginia."""
    cleaned = _CITY_WITH_STATE_WORD.sub(" ", _DC_RE.sub(" district of columbia ", text or ""))
    lowered = " " + re.sub(r"[^a-z]+", " ", cleaned.lower()) + " "
    found: set[str] = set()
    for name in sorted(_STATE_NAME_TO_ABBR, key=len, reverse=True):
        token = f" {name} "
        if token in lowered:
            found.add(_STATE_NAME_TO_ABBR[name])
            lowered = lowered.replace(token, "  ")
    return found


def _states_mentioned(text: str) -> set[str]:
    """State codes explicitly named in a phrase (full names, then upper codes)."""
    found = _state_names_in(text)
    for abbr in US_STATE_NAMES:
        # Only uppercase-as-written codes, so "in"/"or"/"me" are not states.
        if re.search(rf"\b{abbr}\b", text):
            found.add(abbr)
    return found


# Grants.gov lists programs run by U.S. embassies and State Department regional
# bureaus next to domestic ones. They fund work *abroad* ("U.S. Mission to
# Australia", "Bureau of African Affairs") — the far-flung results grant writers
# flagged — and the "U.S." in the name made them look nationwide. The bureau list
# is explicit on purpose: "Bureau of Indian Affairs" is domestic and must pass.
_OVERSEAS_TEXT = re.compile(
    r"\b(?:u\.?s\.?\s+mission\s+to|u\.?s\.?\s+embassy|embassy\s+(?:in|of)|consulate|"
    r"bureau\s+of\s+(?:african|european\s+and\s+eurasian|east\s+asian\s+and\s+pacific|"
    r"near\s+eastern|south\s+and\s+central\s+asian|western\s+hemisphere)\s+affairs)\b",
    re.I,
)


def _overseas_program(row: dict[str, Any]) -> str:
    """Reason text when this is an overseas program, else an empty string."""
    if _text(row.get("agency_code")).upper().startswith("DOS-"):
        return "overseas program run by the U.S. State Department, not a domestic grant"
    signal = " ".join(
        _text(row.get(key)) for key in ("agency", "top_agency", "title", "agency_address")
    )
    if _OVERSEAS_TEXT.search(signal):
        return "overseas program run by a U.S. embassy or mission, not a domestic grant"
    return ""


# Programs for organizations abroad that don't come from a U.S. embassy, e.g.
# "Projects must be proposed by African organizations" or "UK-registered
# charities". Demonyms only count next to an applicant word, so "African
# American youth" stays domestic; "New England" is a U.S. region.
_FOREIGN_PLACE = re.compile(
    r"(?<![-\w])(?:africa|europe|united\s+kingdom|(?<!new\s)england|scotland|canada|australia|"
    r"new\s+zealand|philippines|kenya|nigeria|uganda|ghana|ethiopia|tanzania|rwanda|india|"
    r"pakistan|bangladesh|nepal)\b",
    re.I,
)
_FOREIGN_APPLICANTS = re.compile(
    r"\b(?:african|kenyan|nigerian|ugandan|ghanaian|ethiopian|canadian|australian|british|uk)"
    r"[-\s]+(?:(?:based|registered|led)\s+)?(?:organi[sz]ations?|charit(?:y|ies)|ngos?|churches|"
    r"institutions?|citizens|nationals|residents|non[-\s]?profits?)\b"
    r"|\bfor\s+(?:africans|canadians|australians)\b",
    re.I,
)
_US_APPLICANTS = re.compile(
    r"\bu\.?s\.?[-\s]+(?:based|registered)\b|\bunited\s+states\b|\bin\s+the\s+u\.?s\.?a?\b|"
    r"\bamerican\s+(?:organi[sz]ations?|non[-\s]?profits?|charit(?:y|ies))\b",
    re.I,
)


def _foreign_program(row: dict[str, Any], home: str) -> str:
    """Reason text when the program is for organizations outside the U.S."""
    signal = " ".join(_text(row.get(key)) for key in ("title", "agency", "eligibility"))
    if not (_FOREIGN_APPLICANTS.search(signal) or _FOREIGN_PLACE.search(signal)):
        return ""
    home_name = US_STATE_NAMES.get(home, "").lower()
    if _US_APPLICANTS.search(signal) or (home_name and home_name in signal.lower()):
        return ""
    return "program for organizations outside the United States"


# "City of Amarillo" as funder or applicant area: a city funds its own city.
_LOCAL_GOVERNMENT = re.compile(
    r"\b(?:[Cc]ity|[Tt]own|[Vv]illage|[Tt]ownship)\s+of\s+(?:the\s+)?"
    r"([A-Z][A-Za-z.'\-]*(?:\s+[A-Z][A-Za-z.'\-]*){0,2})"
)


def _other_city_program(row: dict[str, Any], city: str) -> str:
    """Reason text when this is another city's local program, else ""."""
    home = _lower(city)
    if not home:
        return ""
    signal = " ".join(_text(row.get(key)) for key in ("agency", "title", "eligibility"))
    names = [match.group(1) for match in _LOCAL_GOVERNMENT.finditer(signal)]
    if not names or re.search(rf"\b{re.escape(home)}\b", signal.lower()):
        return ""
    words = names[0].split("'")[0].split()
    while len(words) > 1 and words[-1].lower() in _NOT_PLACE_WORDS:
        words.pop()
    return f"local program of the City of {' '.join(words)}, not {_text(city)}"


_NOT_PLACE_WORDS = frozenset(
    "economic development department office mayor council housing community "
    "services planning county commission authority".split()
)


# Well-known cities → state, so "City of Houston" or "San Antonio Area
# Foundation" places a program even when no state is written. Names that are
# also common words or first names (Phoenix House, Charlotte, Savannah) are left
# out on purpose.
_CITY_STATES: dict[str, str] = {
    **{name: code for name, code in CITY_STATE_HINTS.items() if name not in {"queens", "phoenix"}},
    "birmingham": "AL", "montgomery": "AL", "huntsville": "AL",
    "anchorage": "AK", "fairbanks": "AK", "juneau": "AK",
    "tucson": "AZ", "mesa": "AZ", "scottsdale": "AZ", "tempe": "AZ", "flagstaff": "AZ",
    "little rock": "AR",
    "san diego": "CA", "san jose": "CA", "sacramento": "CA", "oakland": "CA", "fresno": "CA",
    "long beach": "CA", "bakersfield": "CA", "anaheim": "CA", "stockton": "CA", "santa ana": "CA",
    "colorado springs": "CO", "boulder": "CO",
    "hartford": "CT", "new haven": "CT", "bridgeport": "CT",
    "orlando": "FL", "tampa": "FL", "jacksonville": "FL", "st. petersburg": "FL",
    "tallahassee": "FL", "fort lauderdale": "FL",
    "honolulu": "HI", "boise": "ID",
    "indianapolis": "IN", "fort wayne": "IN",
    "des moines": "IA", "cedar rapids": "IA", "iowa city": "IA",
    "wichita": "KS", "topeka": "KS", "louisville": "KY",
    "new orleans": "LA", "baton rouge": "LA", "shreveport": "LA",
    "baltimore": "MD",
    "grand rapids": "MI", "lansing": "MI", "ann arbor": "MI", "kalamazoo": "MI",
    "minneapolis": "MN", "saint paul": "MN", "st. paul": "MN", "duluth": "MN",
    "st. louis": "MO", "saint louis": "MO", "kansas city": "MO",
    "billings": "MT", "missoula": "MT", "omaha": "NE",
    "las vegas": "NV", "reno": "NV", "carson city": "NV",
    "newark": "NJ", "jersey city": "NJ", "trenton": "NJ",
    "albuquerque": "NM", "santa fe": "NM",
    "new york city": "NY", "bronx": "NY", "syracuse": "NY", "yonkers": "NY",
    "raleigh": "NC", "greensboro": "NC",
    "fargo": "ND", "bismarck": "ND",
    "cleveland": "OH", "cincinnati": "OH", "toledo": "OH", "akron": "OH", "dayton": "OH",
    "oklahoma city": "OK", "tulsa": "OK",
    "pittsburgh": "PA", "allentown": "PA", "providence": "RI", "sioux falls": "SD",
    "nashville": "TN", "memphis": "TN", "knoxville": "TN", "chattanooga": "TN",
    "san antonio": "TX", "fort worth": "TX", "el paso": "TX", "amarillo": "TX", "lubbock": "TX",
    "corpus christi": "TX", "plano": "TX", "laredo": "TX", "texas city": "TX",
    "salt lake city": "UT", "provo": "UT", "virginia beach": "VA",
    "spokane": "WA", "tacoma": "WA", "milwaukee": "WI", "cheyenne": "WY",
}
_CITY_ALIASES = {
    "nyc": "new york", "new york city": "new york", "brooklyn": "new york",
    "manhattan": "new york", "bronx": "new york", "st. paul": "saint paul", "st. louis": "saint louis",
}
_CITY_RE = re.compile(
    r"(?<![\w.])("
    + "|".join(re.escape(name) for name in sorted(_CITY_STATES, key=len, reverse=True))
    + r")(?!\w)"
)


def _canonical_city(name: str) -> str:
    key = _lower(name)
    return _CITY_ALIASES.get(key, key)


def _cities_in(text: str) -> dict[str, str]:
    """Known cities named in the text → their state code."""
    return {
        _canonical_city(match.group(1)): _CITY_STATES[match.group(1)]
        for match in _CITY_RE.finditer((text or "").lower())
    }


# "Open nationwide", "any state", "across the United States": an explicit
# statement that location does not matter, which outranks a state named in passing.
_NATIONWIDE_TEXT = re.compile(
    r"\bnationwide\b|\ball\s+(?:50\s+)?states\b|\bany\s+(?:u\.s\.\s+)?state\b|"
    r"\b(?:across|throughout)\s+the\s+(?:u\.s\.|united\s+states|country|nation)\b",
    re.I,
)
# Federal or national funders fund applicants in every state unless they say otherwise.
# Acronyms must match as written (so "us" in a title is not "US"); names match
# in any case.
_FEDERAL_ACRONYMS = re.compile(
    r"\bUS\s+(?:Department|Dept|Agency)\b|"
    r"\b(?:USDA|HUD|NASA|NSF|NIH|EPA|FEMA|HHS|ACF|HRSA|SAMHSA|CDC|DOE|DOL|DOJ|DOT|NEH|NEA|IMLS|"
    r"SBA|EDA|NOAA|IHS|BIA)\b"
)
_FEDERAL_NAMES = re.compile(
    r"\bu\.s\.|\bunited\s+states\b|\bfederal\b|\bnational\b|\bamericorps\b|"
    r"\badministration\s+for\s+children\s+and\s+families\b|"
    r"\bdepartment\s+of\s+(?:agriculture|commerce|education|energy|health\s+and\s+human\s+services|"
    r"homeland\s+security|housing\s+and\s+urban\s+development|(?:the\s+)?interior|justice|labor|"
    r"transportation|(?:the\s+)?treasury|veterans\s+affairs)\b",
    re.I,
)
# Phrases that tie a program to a place: "serving counties in West Virginia",
# "nonprofits located in Ohio", "residents of Houston".
_RESTRICTION_WINDOW = re.compile(
    r"\b(?:located|based|headquartered|operating|residing|serving|serves|residents|communities|"
    r"counties|organi[sz]ations|nonprofits|schools|applicants|businesses)\s+"
    r"(?:of|in|within|throughout|across)\s+([^.;\n]{0,80})",
    re.I,
)


def _strict_location() -> bool:
    return os.getenv("GRANT_STRICT_LOCATION", "1").strip().lower() not in {"0", "false", "no", "off"}


def _hide_nationwide() -> bool:
    """
    Grant writers asked for results located in the place they searched only, so a
    federal or nationwide program that is not tied to that place is hidden too.
    GRANT_HIDE_NATIONWIDE=0 shows those programs again.
    """
    return os.getenv("GRANT_HIDE_NATIONWIDE", "1").strip().lower() not in {"0", "false", "no", "off"}


# Blocker text for a program hidden only because it is open nationwide instead
# of tied to the searched place. Such programs are offered separately as
# "Grants you may also apply for" (see grant_agent.recommendation_sections).
NATIONWIDE_ONLY_BLOCKER = "nationwide program, not specific to"


def _field_states(value: Any) -> tuple[set[str], bool]:
    """States in an explicit state field ("CA", "['CA', 'NV']", "Nationwide")."""
    text = _text(value)
    if not text:
        return set(), False
    if _NATIONWIDE_TEXT.search(text) or text.lower() in {"all", "national", "us", "usa"}:
        return set(), True
    codes = {code for code in re.findall(r"\b[A-Z]{2}\b", text.upper()) if code in US_STATE_NAMES}
    return codes | _state_names_in(text), False


def check_geography(row: dict[str, Any], state: str, city: str = "") -> EligibilityVerdict:
    """
    Is this opportunity available where the user asked for?

    Grant writers asked for strict location matching: a program must be tied to
    the requested state (and not to a different city, when one is given). Programs
    tied to another state or city are dropped; a local or foundation program that
    gives no sign of where it funds is dropped (GRANT_STRICT_LOCATION=0 keeps
    those); and a federal or nationwide program that never names the requested
    place is hidden as well (GRANT_HIDE_NATIONWIDE=0 shows those).
    """
    overseas = _overseas_program(row)
    if overseas:
        return EligibilityVerdict(INELIGIBLE, blockers=(overseas,))

    foreign = _foreign_program(row, _text(state).upper())
    if foreign:
        return EligibilityVerdict(INELIGIBLE, blockers=(foreign,))

    home = _text(state).upper()
    if not home:
        return EligibilityVerdict(UNVERIFIED, ("no home state on profile",))
    home_name = US_STATE_NAMES.get(home, home)
    home_city = _canonical_city(city)
    place = f"{_text(city)}, {home}" if home_city else home_name
    strict = _strict_location()
    hide_nationwide = _hide_nationwide()
    not_local = EligibilityVerdict(
        INELIGIBLE, blockers=(f"{NATIONWIDE_ONLY_BLOCKER} {place}",)
    )

    # Explicit place of performance wins when the source provides it.
    field_states, field_nationwide = _field_states(row.get("pop_state") or row.get("state"))
    if field_nationwide:
        return not_local if hide_nationwide else EligibilityVerdict(ELIGIBLE, ("nationwide program",))
    if field_states:
        if home in field_states:
            return EligibilityVerdict(ELIGIBLE, (f"located in {home}",))
        return EligibilityVerdict(
            INELIGIBLE, blockers=(f"restricted to {', '.join(sorted(field_states))}, not {home}",)
        )

    # Who funds it and what it is called are the strongest signals; the
    # eligibility text is next. A state agency funds its own state.
    headline = " ".join(_text(row.get(key)) for key in ("agency", "top_agency", "title"))
    eligibility = _text(row.get("eligibility"))
    description = _text(row.get("description"))
    federal = (
        _lower(row.get("source")) == "grants_gov"
        or bool(_text(row.get("agency_code")))
        or bool(_FEDERAL_ACRONYMS.search(headline) or _FEDERAL_NAMES.search(headline))
    )

    nationwide = bool(_NATIONWIDE_TEXT.search(" ".join((headline, eligibility, description))))
    if nationwide and not hide_nationwide:
        return EligibilityVerdict(ELIGIBLE, ("nationwide program",))

    cities = _cities_in(f"{headline} {eligibility}")
    named = _states_mentioned(headline) | _state_names_in(eligibility) | set(cities.values())
    if not federal:
        # Federal descriptions often say "applicants in Alaska are encouraged"
        # without restricting anyone, so only non-federal text is read this way.
        for window in _RESTRICTION_WINDOW.finditer(description):
            named |= _state_names_in(window.group(1)) | set(_cities_in(window.group(1)).values())

    if named:
        if home not in named:
            others = ", ".join(sorted(named))
            return EligibilityVerdict(INELIGIBLE, blockers=(f"targets {others}, not {home}",))
        # In the right state. When the user named a city, a program local to a
        # different city there is not theirs — unless it is a statewide program
        # (the funder or title names the state itself).
        if strict and home_city and home_city not in cities and home not in _states_mentioned(headline):
            local = sorted(name for name, code in cities.items() if code == home)
            if local:
                return EligibilityVerdict(
                    INELIGIBLE,
                    blockers=(f"local to {local[0].title()}, not {_text(city)}",),
                )
        return EligibilityVerdict(ELIGIBLE, (f"targets {home}",))

    other_city = _other_city_program(row, city)
    if other_city:
        return EligibilityVerdict(INELIGIBLE, blockers=(other_city,))

    if federal and not hide_nationwide:
        return EligibilityVerdict(ELIGIBLE, ("federal program open to all states",))

    everything = f"{headline} {eligibility} {description}"
    if (
        re.search(rf"\b{re.escape(home_name.lower())}\b", everything.lower())
        or (home_city and home_city in _cities_in(everything))
        or (home_city and re.search(rf"\b{re.escape(home_city)}\b", everything.lower()))
        or re.search(rf"\b{home}\b", headline)
    ):
        return EligibilityVerdict(ELIGIBLE, (f"available in {home_name}",))

    if hide_nationwide and (federal or nationwide):
        return not_local

    if strict:
        return EligibilityVerdict(
            INELIGIBLE, blockers=(f"not confirmed to be available in {place}",)
        )
    return EligibilityVerdict(UNVERIFIED, ("no geographic restriction found",))


# ── Gate 3: record type ─────────────────────────────────────────────────────


def check_applicable_record(row: dict[str, Any]) -> EligibilityVerdict:
    """
    Reject records that are not opportunities you can apply for.

    USASpending returns `spending_by_award` rows — money already awarded to a
    named recipient. Those are useful as funding intelligence but must never be
    recommended as something to apply for.
    """
    if _lower(row.get("source")) == "usaspending" or _lower(row.get("doc_type")) == "award":
        recipient = _text(row.get("recipient"))
        detail = f" (already awarded to {recipient})" if recipient else ""
        return EligibilityVerdict(
            INELIGIBLE,
            blockers=(f"historical award, not an open opportunity{detail}",),
        )
    # Aggregator placeholders ("Various local and regional foundations … direct
    # funder must be verified") are not something anyone can apply to.
    funder = " ".join(_text(row.get(key)) for key in ("agency", "top_agency"))
    if _PLACEHOLDER_FUNDER.search(funder):
        return EligibilityVerdict(
            INELIGIBLE,
            blockers=("not a specific opportunity: the funder isn't identified",),
        )
    return EligibilityVerdict(ELIGIBLE, ("open opportunity",))


_PLACEHOLDER_FUNDER = re.compile(
    r"\bvarious\b[^.]{0,60}\b(?:funders?|foundations?|sources|organi[sz]ations|donors|grantmakers)\b"
    r"|\bmust\s+be\s+verified\b|\baggregator\b",
    re.I,
)


# ── Gate: award size vs budget ──────────────────────────────────────────────

_MONEY_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k|mm|m|thousand|million|billion|b)?\b", re.I)
_MONEY_UNITS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}


def _money_amounts(value: Any) -> list[float]:
    amounts: list[float] = []
    for match in _MONEY_RE.finditer(_text(value)):
        try:
            number = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        amounts.append(number * _MONEY_UNITS.get((match.group(2) or "").lower(), 1))
    return amounts


def check_budget_fit(row: dict[str, Any], profile: dict[str, Any]) -> EligibilityVerdict:
    """
    Can an award from this program be the size the user asked for?

    Uses only the published per-award minimum/maximum — never program totals
    like "$6 million total" — and skips $0 / missing values, which mean unknown.
    """
    requested = max(_money_amounts(profile.get("budget_requested")) or [0.0])
    upper = max(_money_amounts(profile.get("budget_max")) or [0.0])
    low = requested or upper
    high = max(upper, low)
    if low <= 0:
        return EligibilityVerdict(UNVERIFIED, ("no budget on profile",))

    floors = [v for v in _money_amounts(row.get("award_floor")) if v > 0]
    ceilings = [v for v in _money_amounts(row.get("award_ceiling")) if v > 0]
    floor = min(floors) if floors else None
    ceiling = max(ceilings) if ceilings else None
    if floor is not None and ceiling is not None and floor > ceiling:
        floor = None
    if floor is not None and floor > high * 2:
        return EligibilityVerdict(
            INELIGIBLE,
            blockers=(f"minimum award ${floor:,.0f} is far above your budget (${high:,.0f})",),
        )
    if ceiling is not None and ceiling < low * 0.25:
        return EligibilityVerdict(
            INELIGIBLE,
            blockers=(f"maximum award ${ceiling:,.0f} is far below your budget (${low:,.0f})",),
        )
    if floor is None and ceiling is None:
        return EligibilityVerdict(UNVERIFIED, ("award size not published",))
    return EligibilityVerdict(ELIGIBLE, ("award size fits your budget",))


# ── Gate 4: topic connection ────────────────────────────────────────────────

# Words that appear in almost every grant and therefore prove nothing.
_TOPIC_STOPWORDS = frozenset(
    """
    the a an and or for of to in on at by with from that this these those
    program programs project projects grant grants grantee funding funded fund
    funds award awards application applications applicant applicants opportunity
    opportunities support supports supporting service services initiative
    initiatives community communities organization organizations agency agencies
    department departments national federal state states local public private
    development office center centers new other others each per any all will
    shall must may can under over about into their there which who whom whose
    year years fiscal
    """.split()
)

_WORD_RE = re.compile(r"[a-z][a-z0-9\-]{2,}")

# Words common enough across grant text that matching one proves nothing on its
# own — they need a distinctive term alongside them to count as a real match.
_WEAK_TOPIC_TERMS = frozenset(
    {
        "school",
        "schools",
        "foundation",
        "foundations",
        "student",
        "students",
        "youth",
        "child",
        "children",
        "family",
        "families",
        "training",
        "education",
        "educational",
        "teacher",
        "teachers",
        "learning",
        "research",
        "health",
        "county",
        "city",
        "rural",
        "small",
        "town",
    }
)


def topic_terms(profile: dict[str, Any]) -> set[str]:
    """Distinctive words describing what the user is actually looking for."""
    blob = " ".join(
        str(profile.get(key) or "")
        for key in ("keyword", "priority_area", "title", "description")
    ).lower()
    return {
        word
        for word in _WORD_RE.findall(blob)
        if word not in _TOPIC_STOPWORDS and len(word) > 3
    }


# Words that pass the stopword filter but still say nothing about a subject.
_SUBJECT_NOISE = frozenset({"after", "before", "based", "level", "area", "areas", "access"})


def subject_terms(text: str) -> set[str]:
    """Distinctive subject words in free text: no stopwords, no generic words."""
    return {
        word
        for word in _WORD_RE.findall((text or "").lower())
        if word not in _TOPIC_STOPWORDS
        and word not in _WEAK_TOPIC_TERMS
        and word not in _SUBJECT_NOISE
        and (len(word) > 3 or any(ch.isdigit() for ch in word))
    }


def check_topic_connection(
    row: dict[str, Any],
    profile: dict[str, Any],
    *,
    minimum_overlap: int = 1,
) -> EligibilityVerdict:
    """
    Does this opportunity have *any* real connection to what was asked for?

    Grant writers were explicit: an opportunity with no meaningful link to the
    stated focus should not appear at all, however well it scores on category or
    location. A water-infrastructure or nursing-research grant shares nothing
    with "K-6 literacy and mentorship" beyond the word "Education".
    """
    from services.query_context import priority_terms

    # Stopwords stay out even when they are part of an area name ("Public
    # Safety", "Community Development") — they prove nothing on their own.
    focus = priority_terms(_text(profile.get("priority_area"))) - _TOPIC_STOPWORDS
    # What the user (or their profile) actually typed. Noise words like "after"
    # pass the stopword filter but mean nothing — "after" matched "Cancer Risk
    # after Bariatric Surgery" in a tutoring search. Digit tokens ("K-8") count.
    typed = (
        topic_terms(profile)
        | subject_terms(
            " ".join(str(profile.get(key) or "") for key in ("keyword", "title", "description"))
        )
    ) - _SUBJECT_NOISE
    wanted = typed | focus
    if not wanted:
        return EligibilityVerdict(UNVERIFIED, ("no focus terms on profile",))

    haystack = " ".join(
        str(row.get(key) or "")
        for key in ("title", "description", "funding_categories", "category", "eligibility")
    ).lower()
    if not haystack.strip():
        return EligibilityVerdict(UNVERIFIED, ("opportunity has no text to match",))

    found = {word for word in _WORD_RE.findall(haystack) if word in wanted}
    if not found:
        return EligibilityVerdict(
            INELIGIBLE,
            blockers=("no meaningful connection to what you asked for",),
        )

    # A single generic word is not a connection: "school" also appears in
    # "schools of nursing", which is how a nursing-research grant slipped into
    # a K-6 literacy search.
    distinctive = found - _WEAK_TOPIC_TERMS
    if distinctive:
        # One focus word, only in the description, on a program whose category is
        # something else ("…through education or economic empowerment" on an
        # Education grant) is a passing mention, not a program about your focus.
        passing = _passing_mention(row, profile, found)
        if passing:
            return EligibilityVerdict(INELIGIBLE, blockers=(passing,))
        sample = ", ".join(sorted(distinctive)[:3])
        return EligibilityVerdict(ELIGIBLE, (f"matches your focus ({sample})",))

    # Only generic words matched ("education", "students"). When the request
    # named something specific ("tutoring", "K-8", "mental"), generic overlap is
    # exactly how unrelated research awards slipped in, so it is not enough.
    specific = typed - _WEAK_TOPIC_TERMS - focus
    if specific:
        return EligibilityVerdict(
            INELIGIBLE,
            blockers=(
                f"shares only generic words ({', '.join(sorted(found)[:2])}), "
                f"not {', '.join(sorted(specific)[:3])}",
            ),
        )

    if len(found) >= max(2, minimum_overlap):
        sample = ", ".join(sorted(found)[:3])
        return EligibilityVerdict(ELIGIBLE, (f"matches your focus ({sample})",))

    # Exception: the generic word IS what the user asked for ("education" in an
    # Education search). Without this, a broad search dropped every grant. It
    # still has to be what the grant is *about* — in its title or funding
    # category — not a passing mention buried in the description.
    own_focus = found & focus
    if own_focus:
        headline = " ".join(
            str(row.get(key) or "") for key in ("title", "funding_categories", "category")
        ).lower()
        if any(word in own_focus for word in _WORD_RE.findall(headline)):
            sample = ", ".join(sorted(own_focus)[:3])
            return EligibilityVerdict(ELIGIBLE, (f"matches your focus ({sample})",))
        return EligibilityVerdict(
            INELIGIBLE,
            blockers=(
                f"mentions {', '.join(sorted(own_focus)[:2])} only in passing, "
                "not a program focused on it",
            ),
        )

    return EligibilityVerdict(
        INELIGIBLE,
        blockers=(
            f"only a generic word in common ({', '.join(sorted(found)[:2])}), "
            "not your actual focus",
        ),
    )


def _passing_mention(row: dict[str, Any], profile: dict[str, Any], found: set[str]) -> str:
    if len(found) != 1:
        return ""
    from services.grant_categories import FALLBACK_CATEGORY, derive_category, normalize_category

    wanted = normalize_category(profile.get("priority_area"))
    actual = normalize_category(row.get("category")) or derive_category(row)
    if not wanted or not actual or actual in (wanted, FALLBACK_CATEGORY):
        return ""
    headline = " ".join(
        str(row.get(key) or "") for key in ("title", "funding_categories", "category")
    ).lower()
    if found & set(_WORD_RE.findall(headline)):
        return ""
    # Neighbouring categories overlap: a literacy program is often filed under
    # Education. There, the focus word itself ("literacy") is a real match; only
    # a loosely related word ("library") is still a passing mention.
    core = set(_WORD_RE.findall(wanted.lower())) - _TOPIC_STOPWORDS
    if actual in _RELATED_CATEGORIES.get(wanted, frozenset()) and found & core:
        return ""
    term = next(iter(found))
    return f"mentions {term} only in passing; this program is about {actual}"


_CATEGORY_GROUPS = (
    {"Education", "Literacy", "Youth Development"},
    {"Economic Development", "Workforce Development", "Community Development"},
    {"Housing", "Community Development", "Human Services"},
    {"Health", "Human Services", "Food Access"},
    {"Arts", "Culture", "Recreation"},
    {"Environment", "Energy", "Agriculture", "Disaster Relief"},
    {"Infrastructure", "Transportation"},
)
_RELATED_CATEGORIES: dict[str, frozenset[str]] = {}
for _group in _CATEGORY_GROUPS:
    for _name in _group:
        _RELATED_CATEGORIES[_name] = _RELATED_CATEGORIES.get(_name, frozenset()) | frozenset(_group)


# ── Combined gate ───────────────────────────────────────────────────────────


def evaluate(row: dict[str, Any], profile: dict[str, Any]) -> EligibilityVerdict:
    """Run every gate. Any single blocker makes the opportunity ineligible."""
    checks = (
        check_applicable_record(row),
        check_applicant_type(row, _text(profile.get("org_type"))),
        check_geography(
            row, _text(profile.get("location_state")), _text(profile.get("location_city"))
        ),
        check_topic_connection(row, profile),
    )

    blockers: list[str] = []
    reasons: list[str] = []
    for verdict in checks:
        blockers.extend(verdict.blockers)
        reasons.extend(verdict.reasons)
    # Budget can rule an opportunity out, but an unpublished award size must not
    # downgrade an otherwise verified one — so it only contributes blockers.
    budget = check_budget_fit(row, profile)
    blockers.extend(budget.blockers)
    if budget.is_eligible:
        reasons.extend(budget.reasons)

    if blockers:
        return EligibilityVerdict(INELIGIBLE, tuple(reasons), tuple(blockers))
    if all(check.is_eligible for check in checks):
        return EligibilityVerdict(ELIGIBLE, tuple(reasons))
    return EligibilityVerdict(UNVERIFIED, tuple(reasons))


def filter_eligible(
    rows: list[dict[str, Any]],
    profile: dict[str, Any],
    *,
    keep_unverified: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Split rows into (kept, dropped).

    Each kept row carries `eligibility_status` and `eligibility_reasons`; each
    dropped row carries `eligibility_blockers` so the exclusion is auditable.
    """
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for row in rows:
        verdict = evaluate(row, profile)
        out = dict(row)
        out["eligibility_status"] = verdict.status
        out["eligibility_reasons"] = "; ".join(verdict.reasons)
        if verdict.is_ineligible:
            out["eligibility_blockers"] = "; ".join(verdict.blockers)
            # Every check passed except "tied to the searched place": the program
            # is open nationwide (no state restriction).
            out["nationwide_only"] = all(
                blocker.startswith(NATIONWIDE_ONLY_BLOCKER) for blocker in verdict.blockers
            )
            dropped.append(out)
            continue
        if verdict.status == UNVERIFIED and not keep_unverified:
            out["eligibility_blockers"] = "eligibility could not be verified"
            dropped.append(out)
            continue
        kept.append(out)
    return kept, dropped
