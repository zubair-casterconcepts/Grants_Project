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

import re
from dataclasses import dataclass
from typing import Any

from services.location_utils import US_STATE_NAMES

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


def _states_mentioned(text: str) -> set[str]:
    """State codes explicitly named in a phrase (full names, then upper codes)."""
    found: set[str] = set()
    lowered = f" {text.lower()} "
    for name, abbr in _STATE_NAME_TO_ABBR.items():
        if f" {name} " in lowered:
            found.add(abbr)
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


def check_geography(row: dict[str, Any], state: str) -> EligibilityVerdict:
    """Is this opportunity locked to a state other than the applicant's?"""
    overseas = _overseas_program(row)
    if overseas:
        return EligibilityVerdict(INELIGIBLE, blockers=(overseas,))

    home = _text(state).upper()
    if not home:
        return EligibilityVerdict(UNVERIFIED, ("no home state on profile",))

    # Explicit place of performance wins when the source provides it.
    pop = _text(row.get("pop_state") or row.get("state")).upper()
    if pop:
        if pop == home:
            return EligibilityVerdict(ELIGIBLE, (f"located in {home}",))
        return EligibilityVerdict(
            INELIGIBLE, blockers=(f"restricted to {pop}, not {home}",)
        )

    # Otherwise infer from the strongest restriction signals: who is funding it
    # and what the opportunity is called. A state agency funds its own state.
    signal = " ".join(
        [
            _text(row.get("agency")),
            _text(row.get("top_agency")),
            _text(row.get("title")),
        ]
    )
    if any(marker in signal.lower() for marker in _NATIONAL_MARKERS):
        return EligibilityVerdict(ELIGIBLE, ("nationwide program",))

    named = _states_mentioned(signal)
    if not named:
        return EligibilityVerdict(UNVERIFIED, ("no geographic restriction found",))
    if home in named:
        return EligibilityVerdict(ELIGIBLE, (f"targets {home}",))
    others = ", ".join(sorted(named))
    return EligibilityVerdict(
        INELIGIBLE, blockers=(f"targets {others}, not {home}",)
    )


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
    return EligibilityVerdict(ELIGIBLE, ("open opportunity",))


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


# ── Combined gate ───────────────────────────────────────────────────────────


def evaluate(row: dict[str, Any], profile: dict[str, Any]) -> EligibilityVerdict:
    """Run every gate. Any single blocker makes the opportunity ineligible."""
    checks = (
        check_applicable_record(row),
        check_applicant_type(row, _text(profile.get("org_type"))),
        check_geography(row, _text(profile.get("location_state"))),
        check_topic_connection(row, profile),
    )

    blockers: list[str] = []
    reasons: list[str] = []
    for verdict in checks:
        blockers.extend(verdict.blockers)
        reasons.extend(verdict.reasons)

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
            dropped.append(out)
            continue
        if verdict.status == UNVERIFIED and not keep_unverified:
            out["eligibility_blockers"] = "eligibility could not be verified"
            dropped.append(out)
            continue
        kept.append(out)
    return kept, dropped
