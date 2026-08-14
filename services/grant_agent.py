"""
OpenAI Agents SDK grant matcher (async-first).

Separate async tools are registered on the agent:
  - grants_gov
  - usaspending
  - granted_ai

Primary path: Agents SDK (`Runner.run` / `run_streamed`) for tool calls and
scoring. Fallback: `asyncio.gather` over the same source clients (no
ThreadPoolExecutor). Sync Django views bridge via a small event-loop helper.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from pydantic import BaseModel, Field

from services.async_utils import run_sync
from services.grant_categories import (
    CATEGORY_CHOICES,
    FALLBACK_CATEGORY,
    derive_category,
    normalize_category,
)
from services.query_context import resolve_search_context, scoring_criteria

logger = logging.getLogger(__name__)

T = TypeVar("T")

_INSTRUCTIONS_PATH = Path(__file__).with_name("grant_agent_instructions.md")

# Drop closed statuses and past deadlines; optionally drop undated rows whose
# open_date is older than GRANT_MAX_OPEN_AGE_MONTHS (default 6).
_CLOSED_STATUS_MARKERS = (
    "closed",
    "archived",
    "inactive",
    "cancelled",
    "canceled",
    "expired",
)
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%d-%b-%Y",
    "%Y/%m/%d",
    "%m/%d/%y",
    "%b %d %Y",
    "%B %d %Y",
)

# Kept as a hard fallback so a missing instructions file never breaks matching.
_DEFAULT_AGENT_INSTRUCTIONS = """# Grant Matching Agent

You are the Grants matching agent. Identify the strongest funding opportunities by querying approved sources, then rank against the user profile.

## Operating flow

1. Review the profile (topic, priority area, location, budget, org type).
2. Call grants_gov, usaspending, and granted_ai in the same turn so they can run concurrently.
3. Pass keyword, priority_area, location_city, and location_state on every tool call.
4. For granted_ai, also pass org_type from the profile when available.
5. Keep only currently open / future opportunities (drop past deadlines and closed/archived statuses), then rank by topic, category, location, then budget.
6. Score each grant 0.0-1.0, set chance_percent to round(score * 100), and add a short reason.
7. Set category to the grant's own subject area (Education, Arts, Health, Housing, etc.).
8. Preserve provider fields from tools (agency, agency_address, contacts, amounts, dates).
9. Return structured matches with source grants_gov, usaspending, or granted_ai.

## Rules

- Do not invent opportunities, agencies, addresses, amounts, deadlines, or URLs.
- Do not return opportunities whose deadline has already passed, or closed/archived statuses.
- If one tool returns no results, continue with the other sources.
"""


def load_agent_instructions() -> str:
    """Load system instructions from the external file, with a safe fallback."""
    try:
        text = _INSTRUCTIONS_PATH.read_text(encoding="utf-8").strip()
        if text:
            return text
        logger.warning("Agent instructions file is empty; using built-in fallback")
    except OSError:
        logger.warning(
            "Could not read %s; using built-in fallback",
            _INSTRUCTIONS_PATH,
            exc_info=True,
        )
    return _DEFAULT_AGENT_INSTRUCTIONS


class GrantMatch(BaseModel):
    source: str = Field(description="grants_gov, usaspending, or granted_ai")
    title: str
    agency: str = ""
    agency_code: str = ""
    agency_address: str = Field(
        default="",
        description="Provider address or contact/location line from the source",
    )
    agency_contact: str = ""
    agency_email: str = ""
    agency_phone: str = ""
    top_agency: str = ""
    deadline: str = ""
    open_date: str = ""
    url: str = ""
    opp_status: str = ""
    number: str = ""
    id: str = ""
    amount: str = ""
    award_ceiling: str = ""
    award_floor: str = ""
    eligibility: str = ""
    description: str = ""
    alns: str = ""
    funding_categories: str = ""
    funding_instruments: str = ""
    category: str = Field(
        default="",
        description=(
            "Subject area of the opportunity, one of: " + ", ".join(CATEGORY_CHOICES)
        ),
    )
    score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fit/quality score from 0 to 1 (higher is better)",
    )
    chance_percent: int = Field(
        default=0,
        ge=0,
        le=100,
        description="Estimated chance of fit as a percentage (score * 100)",
    )
    reason: str = Field(
        default="",
        description="Short reason this opportunity fits the user profile",
    )


class GrantMatchResult(BaseModel):
    matches: list[GrantMatch] = Field(default_factory=list)
    summary: str = ""


class GrantScoreRow(BaseModel):
    index: int
    score: float = Field(ge=0.0, le=1.0)
    chance_tier: str = Field(description="high, medium, or low")
    reason: str = ""
    category: str = Field(
        default="",
        description=(
            "Subject area of the grant itself (not the user's focus), one of: "
            + ", ".join(CATEGORY_CHOICES)
        ),
    )


class GrantScoreResult(BaseModel):
    scores: list[GrantScoreRow] = Field(default_factory=list)


def _run_async(coro: Awaitable[T]) -> T:
    """Run an async coroutine from sync Django code."""
    return run_sync(coro)


def _iter_async_generator(agen: AsyncIterator[T]) -> Iterator[T]:
    """Drive an async generator from sync SSE views without ThreadPoolExecutor."""
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        while True:
            try:
                yield loop.run_until_complete(agen.__anext__())  # type: ignore[attr-defined]
            except StopAsyncIteration:
                break
    finally:
        try:
            loop.run_until_complete(agen.aclose())  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            loop.close()
        finally:
            asyncio.set_event_loop(None)


def _agent_enabled() -> bool:
    """Agents SDK is on by default when an API key exists; set GRANT_USE_AGENT=0 to disable."""
    flag = os.getenv("GRANT_USE_AGENT", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def _matching_prompt(payload: dict[str, Any], user_query: str = "") -> str:
    return (
        "Run the matching flow for this search context. "
        "DEFAULTS come from the saved user profile. "
        "OVERRIDES come from the latest user message — use overrides when present, "
        "otherwise keep profile defaults for that field. "
        "Call grants_gov, usaspending, and granted_ai in the SAME turn so they can "
        "run concurrently. You may omit tool args to use baked-in defaults, or pass "
        "overrides explicitly. "
        "Preserve agency name, agency_address, and other provider fields. "
        "Set chance_percent to round(score * 100).\n\n"
        f"USER_QUERY:\n{(user_query or '').strip() or '(none — use all profile defaults)'}\n\n"
        f"SEARCH_CONTEXT_JSON:\n{json.dumps(payload, indent=2)}"
    )


def _rows_from_tool_output(output: Any) -> list[dict[str, Any]]:
    """Normalize Agents SDK tool output into grant row dicts."""
    if isinstance(output, list):
        return [row for row in output if isinstance(row, dict)]
    if isinstance(output, str) and output.strip():
        try:
            data = json.loads(output)
        except json.JSONDecodeError:
            return []
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
    return []


def _tool_name_from_item(item: Any, call_map: dict[str, str]) -> str:
    name = getattr(item, "tool_name", None)
    if name:
        return str(name)
    origin = getattr(item, "tool_origin", None)
    if origin is not None and getattr(origin, "agent_tool_name", None):
        return str(origin.agent_tool_name)
    call_id = getattr(item, "call_id", None)
    if call_id and call_id in call_map:
        return call_map[str(call_id)]
    raw = getattr(item, "raw_item", None)
    if isinstance(raw, dict):
        return str(raw.get("name") or "")
    return str(getattr(raw, "name", "") or "")


def _profile_payload(profile: Any) -> dict[str, Any]:
    """Saved profile fields only (no per-message overrides)."""
    from services.query_context import profile_defaults

    return profile_defaults(profile)


def _search_context(profile: Any, user_query: str = "") -> dict[str, Any]:
    """Profile defaults + latest user-query overrides for tools/scoring."""
    return resolve_search_context(profile, user_query=user_query or "")


def _compact(items: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    from services.tools._normalize import compact_source_rows

    return compact_source_rows(items, source=source)


def _parse_opportunity_date(value: Any) -> date | None:
    """Best-effort parse of provider deadline/open dates. Returns None if unknown."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    # ISO / datetime prefixes: 2024-03-15 or 2024-03-15T00:00:00Z
    head = text[:10]
    if len(head) == 10 and head[4] == "-" and head[7] == "-":
        try:
            return date.fromisoformat(head)
        except ValueError:
            pass
    # Strip ordinal suffixes: "March 15th, 2024"
    cleaned = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", text, flags=re.I)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for candidate in (cleaned, cleaned.split("T", 1)[0].strip(), cleaned[:32]):
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def _max_open_age_months() -> int:
    raw = os.getenv("GRANT_MAX_OPEN_AGE_MONTHS", "6").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 6


def _is_closed_status(row: dict[str, Any]) -> bool:
    status = str(row.get("opp_status") or "").strip().lower()
    if not status:
        return False
    return any(marker in status for marker in _CLOSED_STATUS_MARKERS)


def _is_actionable_opportunity(row: dict[str, Any], *, today: date | None = None) -> bool:
    """
    Keep currently open / future opportunities only.

    - Closed/archived/expired statuses → drop
    - Parseable deadline before today → drop (even if only days old)
    - No deadline, but open_date older than GRANT_MAX_OPEN_AGE_MONTHS → drop
    - No usable dates → keep (avoid over-filtering unknown formats)
    """
    if _is_closed_status(row):
        return False

    now = today or date.today()
    deadline = _parse_opportunity_date(row.get("deadline"))
    if deadline is not None:
        return deadline >= now

    months = _max_open_age_months()
    if months <= 0:
        return True
    open_date = _parse_opportunity_date(row.get("open_date"))
    if open_date is None:
        return True
    return open_date >= (now - timedelta(days=months * 30))


def _filter_actionable_opportunities(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop past/closed opportunities before scoring or UI emission."""
    if not rows:
        return []
    today = date.today()
    kept = [row for row in rows if _is_actionable_opportunity(row, today=today)]
    dropped = len(rows) - len(kept)
    if dropped:
        logger.info(
            "Freshness filter removed %s stale/closed opportunities (%s kept)",
            dropped,
            len(kept),
        )
    return kept


def _chance_tier(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.55:
        return "medium"
    return "low"


def _chance_label(tier: str) -> str:
    return {
        "high": "High chance",
        "medium": "Medium chance",
        "low": "Lower chance",
    }.get(tier, "Lower chance")


def _attach_display_fields(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ensure score/chance and provider fields are present for the dashboard."""
    prepared: list[dict[str, Any]] = []
    for item in matches:
        row = dict(item)
        try:
            score = float(row.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(1.0, score))
        row["score"] = round(score, 2)
        row["chance_percent"] = int(round(score * 100))
        tier = row.get("chance_tier") or _chance_tier(score)
        row["chance_tier"] = tier
        row["chance_label"] = row.get("chance_label") or _chance_label(str(tier))
        for key in (
            "agency",
            "agency_code",
            "agency_address",
            "agency_contact",
            "agency_email",
            "agency_phone",
            "top_agency",
            "eligibility",
            "description",
            "amount",
            "award_ceiling",
            "award_floor",
            "open_date",
            "alns",
            "funding_categories",
        ):
            row.setdefault(key, "")
            if row[key] is None:
                row[key] = ""
        row["category"] = normalize_category(row.get("category")) or derive_category(row)
        prepared.append(row)
    return prepared


_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")


def _tokens(text: Any) -> set[str]:
    return set(_TOKEN_RE.findall(str(text or "").lower()))


def _parse_money(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    text = str(value)
    # Prefer ranges like "$10,000 – $50,000" → use ceiling (last number).
    nums = re.findall(r"\d[\d,]*(?:\.\d+)?", text.replace(",", ""))
    if not nums:
        return None
    try:
        return float(nums[-1])
    except ValueError:
        return None


def _topic_score(profile: dict[str, Any], row: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    profile_bits = " ".join(
        [
            str(profile.get("title") or ""),
            str(profile.get("description") or ""),
            str(profile.get("priority_area") or ""),
            str(profile.get("eligibility_notes") or ""),
        ]
    )
    grant_bits = " ".join(
        [
            str(row.get("title") or ""),
            str(row.get("description") or ""),
            str(row.get("funding_categories") or ""),
            str(row.get("eligibility") or ""),
            str(row.get("agency") or ""),
        ]
    )
    p_tokens = _tokens(profile_bits)
    g_tokens = _tokens(grant_bits)
    if not p_tokens or not g_tokens:
        return 0.28, reasons

    overlap = p_tokens & g_tokens
    # Jaccard-ish but generous for short titles.
    ratio = len(overlap) / max(6, min(len(p_tokens), 18))
    score = min(1.0, ratio * 1.35)

    priority = str(profile.get("priority_area") or "").strip().lower()
    if priority and priority in grant_bits.lower():
        score = min(1.0, score + 0.22)
        reasons.append(f"topic matches {profile.get('priority_area')}")
    elif overlap:
        reasons.append("topic keywords overlap")

    title = str(profile.get("title") or "").strip().lower()
    grant_title = str(row.get("title") or "").lower()
    if title and any(tok in grant_title for tok in _tokens(title) if len(tok) > 3):
        score = min(1.0, score + 0.12)

    return score, reasons


def _location_score(profile: dict[str, Any], row: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    p_state = str(profile.get("location_state") or "").strip().upper()
    p_city = str(profile.get("location_city") or "").strip().lower()
    hay = " ".join(
        [
            str(row.get("pop_state") or ""),
            str(row.get("state") or ""),
            str(row.get("agency_address") or ""),
            str(row.get("pop_city") or ""),
            str(row.get("pop_country") or ""),
            str(row.get("title") or ""),
            str(row.get("description") or ""),
        ]
    ).lower()

    if not p_state and not p_city:
        return 0.45, reasons

    score = 0.2
    if p_state and (
        p_state.lower() in hay
        or f" {p_state.lower()} " in f" {hay} "
        or str(row.get("pop_state") or "").strip().upper() == p_state
        or str(row.get("state") or "").strip().upper() == p_state
    ):
        score = 0.9
        reasons.append(f"location matches {p_state}")
    if p_city and p_city in hay:
        score = min(1.0, score + 0.1)
        if "location matches" not in " ".join(reasons):
            reasons.append(f"near {profile.get('location_city')}")

    # Nationwide / no location constraint still acceptable.
    if score <= 0.2 and any(
        key in hay for key in ("nationwide", "national", "all states", "united states", "u.s.")
    ):
        score = 0.55
        reasons.append("national opportunity")

    return score, reasons


def _budget_score(profile: dict[str, Any], row: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    try:
        requested = float(
            str(profile.get("budget_requested") or "")
            .replace(",", "")
            .replace("$", "")
            .strip()
            or 0
        )
    except (TypeError, ValueError, InvalidOperation):
        requested = 0.0

    ceiling = _parse_money(row.get("award_ceiling") or row.get("amount"))
    floor = _parse_money(row.get("award_floor"))

    if requested <= 0:
        return 0.5, reasons
    if ceiling is None and floor is None:
        return 0.48, reasons

    score = 0.4
    if ceiling is not None and requested <= ceiling * 1.15:
        score = 0.85
        reasons.append("budget within award range")
    elif ceiling is not None and requested <= ceiling * 2.0:
        score = 0.62
        reasons.append("budget near award range")
    elif floor is not None and requested >= floor * 0.5:
        score = 0.58
        reasons.append("budget compatible with award floor")
    else:
        score = 0.35

    return score, reasons


def _status_score(row: dict[str, Any]) -> tuple[float, list[str]]:
    status = str(row.get("opp_status") or "").strip().lower()
    if not status:
        return 0.55, []
    if any(x in status for x in ("posted", "open", "forecast", "active", "accepting")):
        return 0.9, ["currently open/posted"]
    if any(x in status for x in ("closed", "archived", "inactive", "cancelled")):
        return 0.25, ["status less actionable"]
    return 0.55, []


def _org_score(profile: dict[str, Any], row: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    org = str(profile.get("org_type") or "").strip().lower()
    elig = " ".join(
        [str(row.get("eligibility") or ""), str(row.get("description") or "")]
    ).lower()
    if not org or not elig:
        return 0.5, reasons

    mapping = {
        "501c3": ("nonprofit", "non-profit", "501(c)(3)", "501c3", "charitable"),
        "government": ("government", "state", "local", "municipal", "county", "tribal"),
        "school": ("school", "education", "university", "college", "lea", "district"),
        "other": (),
    }
    needles = mapping.get(org, ())
    if any(n in elig for n in needles):
        reasons.append("eligibility fits org type")
        return 0.88, reasons
    return 0.48, reasons


def _score_grant_against_profile(
    row: dict[str, Any],
    profile: dict[str, Any],
    *,
    rank_index: int = 0,
) -> dict[str, Any]:
    """
    Relevance score from tool fields vs user profile.
    Weights: topic 38%, location 27%, budget 18%, status 10%, org/eligibility 7%.
    Optional API fit_score is blended lightly when present.
    """
    topic, topic_reasons = _topic_score(profile, row)
    location, loc_reasons = _location_score(profile, row)
    budget, budget_reasons = _budget_score(profile, row)
    status, status_reasons = _status_score(row)
    org, org_reasons = _org_score(profile, row)

    score = (
        topic * 0.38
        + location * 0.27
        + budget * 0.18
        + status * 0.10
        + org * 0.07
    )

    fit = row.get("fit_score")
    if fit not in (None, ""):
        try:
            fit_f = max(0.0, min(1.0, float(fit)))
            score = (score * 0.75) + (fit_f * 0.25)
        except (TypeError, ValueError):
            pass

    # Tiny rank tie-breaker so stable ordering within equal scores.
    score = max(0.05, min(0.99, score - (rank_index * 0.004)))

    reasons = topic_reasons + loc_reasons + budget_reasons + status_reasons + org_reasons
    if not reasons:
        reasons = ["general relevance to your project filters"]

    out = dict(row)
    out["score"] = round(score, 2)
    out["chance_percent"] = int(round(score * 100))
    tier = _chance_tier(score)
    out["chance_tier"] = tier
    out["chance_label"] = _chance_label(tier)
    out["reason"] = "; ".join(reasons[:3])
    out["score_method"] = "local"
    out.setdefault("amount", out.get("amount") or "")
    return out


def _ai_scoring_enabled() -> bool:
    flag = os.getenv("GRANT_USE_AI_SCORE", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def _rank_by_chance(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tier_rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        matches,
        key=lambda m: (
            tier_rank.get(str(m.get("chance_tier") or ""), 3),
            -float(m.get("score") or 0),
        ),
    )


def _grant_score_payload(row: dict[str, Any], index: int) -> dict[str, Any]:
    """Compact grant fields sent to the AI scorer."""
    desc = str(row.get("description") or "")[:450]
    return {
        "index": index,
        "source": row.get("source") or "",
        "title": row.get("title") or "",
        "agency": row.get("agency") or row.get("top_agency") or "",
        "description": desc,
        "funding_categories": row.get("funding_categories") or "",
        "category_hint": row.get("category") or "",
        "eligibility": str(row.get("eligibility") or "")[:280],
        "amount": row.get("amount") or "",
        "award_ceiling": row.get("award_ceiling") or "",
        "award_floor": row.get("award_floor") or "",
        "deadline": row.get("deadline") or "",
        "opp_status": row.get("opp_status") or "",
        "pop_city": row.get("pop_city") or "",
        "pop_state": row.get("pop_state") or row.get("state") or "",
    }


def _neutral_rows(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Unscored merge rows (no local relevance scoring)."""
    prepared: list[dict[str, Any]] = []
    for item in matches:
        row = dict(item)
        fit = row.get("fit_score")
        score = None
        if fit not in (None, ""):
            try:
                score = max(0.0, min(1.0, float(fit)))
            except (TypeError, ValueError):
                score = None
        if score is None:
            score = 0.5
        row["score"] = round(score, 2)
        row["chance_percent"] = int(round(score * 100))
        tier = _chance_tier(score)
        row["chance_tier"] = tier
        row["chance_label"] = _chance_label(tier)
        row["score_method"] = "pending"
        row.setdefault("reason", row.get("match_reasons") or "Awaiting AI ranking.")
        row.setdefault("amount", row.get("amount") or "")
        prepared.append(row)
    return _attach_display_fields(prepared)


# Chat keeps a compact board; digests can ask for a higher `result_limit`.
DEFAULT_RESULT_LIMIT = 12
DEFAULT_CANDIDATE_LIMIT = 18


def _merge_score_rows(
    matches: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    score_rows: list[Any],
    *,
    result_limit: int = DEFAULT_RESULT_LIMIT,
) -> list[dict[str, Any]]:
    """Apply structured score rows onto candidate grants."""
    limit = max(1, int(result_limit or DEFAULT_RESULT_LIMIT))
    by_index: dict[int, dict[str, Any]] = {}
    for item in score_rows:
        if isinstance(item, GrantScoreRow):
            by_index[item.index] = item.model_dump()
            continue
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        by_index[idx] = item

    if not by_index:
        return _attach_display_fields(candidates[:limit])

    rescored: list[dict[str, Any]] = []
    for i, row in enumerate(candidates):
        out = dict(row)
        ai = by_index.get(i)
        if not ai:
            rescored.append(out)
            continue
        try:
            score = max(0.0, min(1.0, float(ai.get("score"))))
        except (TypeError, ValueError):
            rescored.append(out)
            continue
        tier = str(ai.get("chance_tier") or "").strip().lower()
        if tier not in {"high", "medium", "low"}:
            tier = _chance_tier(score)
        reason = str(ai.get("reason") or "").strip()[:180]
        category = normalize_category(ai.get("category"))
        if category and category != FALLBACK_CATEGORY:
            out["category"] = category
        out["score"] = round(score, 2)
        out["chance_percent"] = int(round(score * 100))
        out["chance_tier"] = tier
        out["chance_label"] = _chance_label(tier)
        if reason:
            out["reason"] = reason
        out["score_method"] = "ai"
        rescored.append(out)

    if len(matches) > len(candidates):
        rescored.extend(_neutral_rows(matches[len(candidates) :]))

    return _attach_display_fields(_rank_by_chance(rescored)[:limit])


async def _apply_ai_scores_async(
    matches: list[dict[str, Any]],
    context: dict[str, Any],
    *,
    result_limit: int = DEFAULT_RESULT_LIMIT,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> list[dict[str, Any]]:
    """
    AI-only scoring via Agents SDK (preferred) against active search criteria.
    Falls back to AsyncOpenAI chat completions, then neutral ordering.
    """
    if not matches:
        return matches
    limit = max(1, int(result_limit or DEFAULT_RESULT_LIMIT))
    pool = max(limit, int(candidate_limit or DEFAULT_CANDIDATE_LIMIT))
    baseline = _neutral_rows(list(matches)[:pool])
    if not _ai_scoring_enabled():
        return _attach_display_fields(_rank_by_chance(baseline)[:limit])

    criteria = scoring_criteria(context if isinstance(context, dict) else {})
    candidates = list(baseline)
    payload = {
        "user_query": criteria.get("user_query") or "",
        "search_criteria": criteria,
        "category_options": list(CATEGORY_CHOICES),
        "grants": [_grant_score_payload(row, i) for i, row in enumerate(candidates)],
    }
    score_prompt = (
        "Score these grants against search_criteria / user_query only. "
        "Never use a conflicting older location/topic/budget outside search_criteria. "
        "Include every grant index exactly once. "
        "chance_tier: high>=0.75, medium>=0.55, else low. "
        "reason max 140 chars. "
        "category: the grant's own subject area, copied exactly from "
        "category_options (keep category_hint unless it is clearly wrong).\n\n"
        f"{json.dumps(payload, ensure_ascii=True)}"
    )

    # 1) Agents SDK scoring agent (no tools — structured output task).
    try:
        from agents import Agent, Runner

        scoring_agent = Agent(
            name="Grant Scoring Agent",
            instructions=(
                "You score grant opportunities for fit/chance against SEARCH_CRITERIA only. "
                "The user_query is the primary intent. search_criteria already applies "
                "query overrides on top of profile defaults. "
                "Score using topic/priority, location, budget, eligibility/org type, status. "
                "Also label each grant's own subject area using category_options. "
                "Return structured scores for every grant index."
            ),
            tools=[],
            output_type=GrantScoreResult,
            model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
        )
        result = await Runner.run(scoring_agent, score_prompt, max_turns=4)
        final = result.final_output
        if isinstance(final, GrantScoreResult) and final.scores:
            return _merge_score_rows(
                matches, candidates, final.scores, result_limit=limit
            )
        if isinstance(final, dict) and final.get("scores"):
            return _merge_score_rows(
                matches, candidates, final["scores"], result_limit=limit
            )
        logger.warning("Scoring agent returned no scores; trying AsyncOpenAI fallback")
    except Exception:
        logger.exception("Agents SDK scoring failed; trying AsyncOpenAI fallback")

    # 2) AsyncOpenAI JSON fallback (keeps ranking working if agent path fails).
    model = os.getenv("OPENAI_MODEL", "gpt-5.5").strip() or "gpt-5.5"
    system = (
        "You score grant opportunities for fit/chance against SEARCH_CRITERIA only. "
        "Return JSON: {\"scores\":[{\"index\":0,\"score\":0.0,\"chance_tier\":\"high|medium|low\","
        "\"reason\":\"...\",\"category\":\"...\"}]}. "
        "Include every grant index exactly once. score is 0.0-1.0. "
        "chance_tier: high>=0.75, medium>=0.55, else low. "
        "category must be copied exactly from category_options."
    )
    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip() or None)
        request_kwargs: dict[str, Any] = {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": score_prompt},
            ],
        }
        temp_raw = os.getenv("OPENAI_SCORE_TEMPERATURE", "").strip()
        if temp_raw:
            try:
                request_kwargs["temperature"] = float(temp_raw)
            except ValueError:
                pass
        try:
            response = await client.chat.completions.create(**request_kwargs)
        except Exception as exc:
            if "temperature" in request_kwargs and "temperature" in str(exc).lower():
                request_kwargs.pop("temperature", None)
                response = await client.chat.completions.create(**request_kwargs)
            else:
                raise
        content = (response.choices[0].message.content or "").strip()
        data = json.loads(content)
        score_rows = data.get("scores") if isinstance(data, dict) else None
        if not isinstance(score_rows, list) or not score_rows:
            logger.warning("AI scorer returned no scores; using unscored order")
            return _attach_display_fields(baseline[:limit])
        return _merge_score_rows(
            matches, candidates, score_rows, result_limit=limit
        )
    except Exception:
        logger.exception("AI grant scoring failed; using unscored order")
        return _attach_display_fields(baseline[:limit])


def _apply_ai_scores(
    matches: list[dict[str, Any]],
    context: dict[str, Any],
    *,
    result_limit: int = DEFAULT_RESULT_LIMIT,
    candidate_limit: int = DEFAULT_CANDIDATE_LIMIT,
) -> list[dict[str, Any]]:
    """Sync bridge for AI scoring."""
    return _run_async(
        _apply_ai_scores_async(
            matches,
            context,
            result_limit=result_limit,
            candidate_limit=candidate_limit,
        )
    )


def _finalize_ranked_matches(
    matches: list[dict[str, Any]],
    context: dict[str, Any],
    *,
    result_limit: int = DEFAULT_RESULT_LIMIT,
) -> list[dict[str, Any]]:
    """AI scoring only, against query/effective search criteria."""
    limit = max(1, int(result_limit or DEFAULT_RESULT_LIMIT))
    pool = max(limit, DEFAULT_CANDIDATE_LIMIT)
    fresh = _filter_actionable_opportunities(list(matches))
    return _apply_ai_scores(
        fresh[:pool],
        context,
        result_limit=limit,
        candidate_limit=pool,
    )


async def _finalize_ranked_matches_async(
    matches: list[dict[str, Any]],
    context: dict[str, Any],
    *,
    result_limit: int = DEFAULT_RESULT_LIMIT,
) -> list[dict[str, Any]]:
    limit = max(1, int(result_limit or DEFAULT_RESULT_LIMIT))
    pool = max(limit, DEFAULT_CANDIDATE_LIMIT)
    fresh = _filter_actionable_opportunities(list(matches))
    return await _apply_ai_scores_async(
        fresh[:pool],
        context,
        result_limit=limit,
        candidate_limit=pool,
    )


def _merge_source_details(
    matches: list[dict[str, Any]],
    sources: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Fill missing agency/detail fields from source rows when the agent omits them."""
    catalog: dict[str, dict[str, Any]] = {}
    for rows in sources.values():
        for item in rows:
            key = f"{item.get('source')}:{item.get('id') or item.get('number') or ''}"
            if key.endswith(":"):
                continue
            catalog[key] = item

    merged: list[dict[str, Any]] = []
    for match in matches:
        row = dict(match)
        key = f"{row.get('source')}:{row.get('id') or row.get('number') or ''}"
        detail = catalog.get(key)
        if detail:
            for field, value in detail.items():
                if field in {"score", "reason", "chance_percent"}:
                    continue
                if (not row.get(field)) and value not in (None, ""):
                    row[field] = value
        merged.append(row)
    return merged


def fetch_both_sources(profile: Any) -> dict[str, list[dict[str, Any]]]:
    """Backward-compatible alias for fetch_all_sources()."""
    return fetch_all_sources(profile)


def _source_coroutines(
    profile: Any,
    user_query: str = "",
    context: dict[str, Any] | None = None,
    *,
    result_limit: int = DEFAULT_RESULT_LIMIT,
) -> dict[str, Callable[[], Awaitable[list[dict[str, Any]]]]]:
    """Build async source fetchers (native async HTTP clients)."""
    from services.granted_ai import search_grants_async
    from services.grants_gov import search_with_filters_async
    from services.usaspending import search_awards_async

    payload = context or _search_context(profile, user_query)
    keyword = payload.get("keyword") or payload.get("title") or "grant"
    city = payload.get("location_city") or ""
    state = payload.get("location_state") or ""
    priority = payload.get("priority_area") or ""
    org_type = payload.get("org_type") or ""
    # Digests ask for larger boards — pull a wider source pool to fill them.
    wide = max(1, int(result_limit or DEFAULT_RESULT_LIMIT)) > DEFAULT_RESULT_LIMIT
    gov_rows = 30 if wide else 15
    usa_limit = 20 if wide else 10
    granted_limit = 25 if wide else 10

    async def _gov() -> list[dict[str, Any]]:
        try:
            results = await search_with_filters_async(
                keyword=keyword,
                priority_area=priority,
                location_city=city,
                location_state=state,
                rows=gov_rows,
            )
            return _compact(results, "grants_gov")
        except Exception:
            logger.warning("grants_gov fallback fetch failed", exc_info=True)
            return []

    async def _usa() -> list[dict[str, Any]]:
        try:
            results = await search_awards_async(
                keyword=keyword,
                priority_area=priority,
                location_city=city,
                location_state=state,
                limit=usa_limit,
            )
            return _compact(results, "usaspending")
        except Exception:
            logger.warning("usaspending fallback fetch failed", exc_info=True)
            return []

    async def _granted() -> list[dict[str, Any]]:
        try:
            results = await search_grants_async(
                keyword=keyword,
                priority_area=priority,
                location_city=city,
                location_state=state,
                org_type=org_type,
                limit=granted_limit,
            )
            return _compact(results, "granted_ai")
        except Exception:
            logger.warning("granted_ai fallback fetch failed", exc_info=True)
            return []

    return {
        "grants_gov": _gov,
        "usaspending": _usa,
        "granted_ai": _granted,
    }


# Backward-compatible alias used by older call sites / commands.
_source_jobs = _source_coroutines


async def fetch_all_sources_async(
    profile: Any,
    user_query: str = "",
    context: dict[str, Any] | None = None,
    *,
    result_limit: int = DEFAULT_RESULT_LIMIT,
) -> dict[str, list[dict[str, Any]]]:
    """Fetch all source APIs concurrently via asyncio.gather (no ThreadPoolExecutor)."""
    jobs = _source_coroutines(
        profile,
        user_query=user_query,
        context=context,
        result_limit=result_limit,
    )
    names = list(jobs.keys())
    results = await asyncio.gather(
        *(jobs[name]() for name in names),
        return_exceptions=True,
    )
    out: dict[str, list[dict[str, Any]]] = {
        "grants_gov": [],
        "usaspending": [],
        "granted_ai": [],
    }
    for name, result in zip(names, results):
        if isinstance(result, Exception):
            logger.warning("%s fetch crashed: %s", name, result)
            out[name] = []
        else:
            out[name] = result or []
    return out


def fetch_all_sources(
    profile: Any,
    user_query: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Sync bridge for concurrent multi-source fetch."""
    return _run_async(
        fetch_all_sources_async(profile, user_query=user_query, context=context)
    )


_SOURCE_LABELS = {
    "grants_gov": "Grants.gov",
    "usaspending": "USASpending",
    "granted_ai": "GrantedAI",
}


def _score_one_source(
    source: str,
    rows: list[dict[str, Any]],
    profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Score a single source for progressive UI (local peek; final merge uses AI)."""
    if source == "grants_gov":
        scored = _score_merge(rows, [], [], profile=profile, ai_priority=False)
    elif source == "usaspending":
        scored = _score_merge([], rows, [], profile=profile, ai_priority=False)
    elif source == "granted_ai":
        scored = _score_merge([], [], rows, profile=profile, ai_priority=False)
    else:
        scored = []
    return scored[:5]


def _status_override_note(payload: dict[str, Any]) -> str:
    overrides = payload.get("overrides") or {}
    if not overrides:
        return " Using your saved profile defaults."
    bits = []
    if overrides.get("location_state") or overrides.get("location_city"):
        place = ", ".join(
            x
            for x in (
                payload.get("location_city") or "",
                payload.get("location_state") or "",
            )
            if x
        )
        bits.append(place or "updated location")
    if overrides.get("priority_area"):
        bits.append(str(overrides["priority_area"]))
    if overrides.get("keyword") or overrides.get("title"):
        bits.append(str(payload.get("keyword") or overrides.get("title")))
    return f" Using your request for {', '.join(bits)}." if bits else ""


def _initial_status_event(payload: dict[str, Any], *, agent: bool) -> dict[str, Any]:
    location = {
        "city": payload.get("location_city") or "",
        "state": payload.get("location_state") or "",
    }
    overrides = payload.get("overrides") or {}
    if agent:
        message = (
            "Agent is querying Grants.gov, USASpending, and GrantedAI concurrently…"
            + _status_override_note(payload)
        )
    else:
        message = (
            "Searching Grants.gov, USASpending, and GrantedAI concurrently…"
            + _status_override_note(payload)
        )
    return {
        "type": "status",
        "message": message,
        "location": location,
        "search_context": {
            "keyword": payload.get("keyword") or "",
            "priority_area": payload.get("priority_area") or "",
            "location_city": location["city"],
            "location_state": location["state"],
            "budget_requested": payload.get("budget_requested") or "",
            "org_type": payload.get("org_type") or "",
            "overrides": overrides,
        },
    }


def _done_event(
    final: list[dict[str, Any]],
    location: dict[str, str],
) -> dict[str, Any]:
    high = sum(1 for m in final if m.get("chance_tier") == "high")
    medium = sum(1 for m in final if m.get("chance_tier") == "medium")
    if final:
        done_message = (
            f"Ranked {len(final)} opportunities with AI scoring "
            f"({high} high, {medium} medium chance)."
        )
    else:
        done_message = "No ranked matches yet for your project."
    return {
        "type": "done",
        "message": done_message,
        "matches": final,
        "match_count": len(final),
        "location": location,
    }


async def _aiter_fallback_events(
    profile: Any,
    user_query: str,
    payload: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    """Async gather fallback that preserves progressive SSE source events."""
    location = {
        "city": payload.get("location_city") or "",
        "state": payload.get("location_state") or "",
    }
    yield _initial_status_event(payload, agent=False)

    jobs = _source_coroutines(profile, user_query=user_query, context=payload)
    collected: dict[str, list[dict[str, Any]]] = {
        "grants_gov": [],
        "usaspending": [],
        "granted_ai": [],
    }
    tasks = {name: asyncio.create_task(fn()) for name, fn in jobs.items()}
    name_by_task = {task: name for name, task in tasks.items()}
    pending: set[asyncio.Task] = set(tasks.values())
    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            source = name_by_task[task]
            label = _SOURCE_LABELS.get(source, source)
            try:
                rows = task.result() or []
            except Exception:
                logger.warning("%s stream fetch failed", source, exc_info=True)
                rows = []
            collected[source] = rows
            scored = _score_one_source(source, rows, profile=payload)
            yield {
                "type": "source",
                "source": source,
                "label": label,
                "message": (
                    f"Found {len(scored)} from {label}."
                    if scored
                    else f"No matches from {label}."
                ),
                "matches": scored,
                "count": len(scored),
                "location": location,
            }

    final = _score_merge(
        collected["grants_gov"],
        collected["usaspending"],
        collected["granted_ai"],
        profile=payload,
        ai_priority=False,
    )
    if final:
        yield {
            "type": "status",
            "message": (
                "Scoring matches with AI against your request"
                + (
                    f" ({payload.get('location_state') or 'your location'}"
                    f", {payload.get('priority_area') or 'your topic'})…"
                    if payload.get("user_query")
                    else " (saved profile defaults)…"
                )
            ),
            "location": location,
        }
        final = await _finalize_ranked_matches_async(final, payload)
    yield _done_event(final, location)


async def _aiter_agent_events(
    profile: Any,
    user_query: str,
    payload: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    """Primary path: Agents SDK streamed tool calls + scoring agent."""
    from agents import Runner
    from agents.items import ToolCallItem, ToolCallOutputItem
    from agents.stream_events import RunItemStreamEvent

    location = {
        "city": payload.get("location_city") or "",
        "state": payload.get("location_state") or "",
    }
    yield _initial_status_event(payload, agent=True)

    agent = build_grant_agent(payload)
    result = Runner.run_streamed(
        agent,
        _matching_prompt(payload, user_query),
        max_turns=12,
    )

    collected: dict[str, list[dict[str, Any]]] = {
        "grants_gov": [],
        "usaspending": [],
        "granted_ai": [],
    }
    call_map: dict[str, str] = {}
    emitted_sources: set[str] = set()

    async for event in result.stream_events():
        if not isinstance(event, RunItemStreamEvent):
            continue
        if event.name == "tool_called" and isinstance(event.item, ToolCallItem):
            tool_name = _tool_name_from_item(event.item, call_map)
            call_id = getattr(event.item, "call_id", None)
            if call_id and tool_name:
                call_map[str(call_id)] = tool_name
            label = _SOURCE_LABELS.get(tool_name, tool_name or "source")
            yield {
                "type": "status",
                "message": f"Agent calling {label}…",
                "location": location,
            }
            continue

        if event.name != "tool_output" or not isinstance(event.item, ToolCallOutputItem):
            continue

        tool_name = _tool_name_from_item(event.item, call_map)
        if tool_name not in collected:
            rows_probe = _rows_from_tool_output(event.item.output)
            if rows_probe:
                tool_name = str(rows_probe[0].get("source") or tool_name)
        if tool_name not in collected:
            continue

        rows = _rows_from_tool_output(event.item.output)
        collected[tool_name] = rows
        if tool_name in emitted_sources:
            continue
        emitted_sources.add(tool_name)
        scored = _score_one_source(tool_name, rows, profile=payload)
        label = _SOURCE_LABELS.get(tool_name, tool_name)
        yield {
            "type": "source",
            "source": tool_name,
            "label": label,
            "message": (
                f"Found {len(scored)} from {label}."
                if scored
                else f"No matches from {label}."
            ),
            "matches": scored,
            "count": len(scored),
            "location": location,
        }

    final_output = result.final_output
    matches: list[dict[str, Any]] = []
    if isinstance(final_output, GrantMatchResult):
        matches = [m.model_dump() for m in final_output.matches]
    elif isinstance(final_output, dict) and "matches" in final_output:
        matches = list(final_output.get("matches") or [])
    matches = _filter_actionable_opportunities(matches)

    if not matches:
        matches = _score_merge(
            collected["grants_gov"],
            collected["usaspending"],
            collected["granted_ai"],
            profile=payload,
            ai_priority=False,
        )
    else:
        used = {m.get("source") for m in matches}
        needed = {"grants_gov", "usaspending", "granted_ai"}
        if not needed.issubset(used):
            sources = await fetch_all_sources_async(
                profile, user_query=user_query, context=payload
            )
            matches = _merge_source_details(matches, sources)
            extras: list[dict[str, Any]] = []
            if sources["granted_ai"] and "granted_ai" not in used:
                extras.extend(
                    _score_merge(
                        [], [], sources["granted_ai"], profile=payload, ai_priority=False
                    )[:4]
                )
            if sources["usaspending"] and "usaspending" not in used:
                extras.extend(
                    _score_merge(
                        [], sources["usaspending"], [], profile=payload, ai_priority=False
                    )[:4]
                )
            if sources["grants_gov"] and "grants_gov" not in used:
                extras.extend(
                    _score_merge(
                        sources["grants_gov"], [], [], profile=payload, ai_priority=False
                    )[:4]
                )
            if extras:
                matches.extend(extras)

    if matches:
        yield {
            "type": "status",
            "message": (
                "Scoring matches with AI against your request"
                + (
                    f" ({payload.get('location_state') or 'your location'}"
                    f", {payload.get('priority_area') or 'your topic'})…"
                    if payload.get("user_query")
                    else " (saved profile defaults)…"
                )
            ),
            "location": location,
        }
        matches = await _finalize_ranked_matches_async(matches, payload)
    yield _done_event(matches, location)


async def aiter_grant_matching_events(
    profile: Any,
    user_query: str = "",
) -> AsyncIterator[dict[str, Any]]:
    """
    Async progressive match events for SSE.
    Primary: Agents SDK streamed tool calls. Fallback: asyncio.gather sources.
    """
    payload = _search_context(profile, user_query)
    if _agent_enabled():
        try:
            async for event in _aiter_agent_events(profile, user_query, payload):
                yield event
            return
        except Exception:
            logger.exception(
                "Async Agents SDK stream failed; falling back to asyncio gather"
            )

    async for event in _aiter_fallback_events(profile, user_query, payload):
        yield event


def iter_grant_matching_events(
    profile: Any,
    user_query: str = "",
) -> Iterator[dict[str, Any]]:
    """Sync bridge for Django StreamingHttpResponse SSE."""
    yield from _iter_async_generator(
        aiter_grant_matching_events(profile, user_query=user_query)
    )


def _collect_source_rows(
    items: list[dict[str, Any]],
    *,
    default_reason: str,
) -> list[dict[str, Any]]:
    """Collect tool rows without local relevance scoring."""
    rows: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        row.setdefault("reason", row.get("match_reasons") or default_reason)
        row.setdefault("amount", row.get("amount") or "")
        rows.append(row)
    return rows


def _score_merge(
    gov: list[dict[str, Any]],
    usa: list[dict[str, Any]],
    granted: list[dict[str, Any]] | None = None,
    profile: dict[str, Any] | Any | None = None,
    *,
    ai_priority: bool = True,
    result_limit: int = DEFAULT_RESULT_LIMIT,
) -> list[dict[str, Any]]:
    """
    Merge sources, then AI-score against query/effective search criteria.
    No local string scoring. `profile` may be a model or search-context dict.
    """
    if profile is not None and not isinstance(profile, dict):
        context = _search_context(profile, "")
    else:
        context = profile or {}

    limit = max(1, int(result_limit or DEFAULT_RESULT_LIMIT))
    pool = max(limit, DEFAULT_CANDIDATE_LIMIT)

    merged: list[dict[str, Any]] = []
    merged.extend(
        _collect_source_rows(
            gov,
            default_reason="Open opportunity from Grants.gov matching your filters.",
        )
    )
    merged.extend(
        _collect_source_rows(
            usa,
            default_reason="USASpending award in/near your location matching your topic.",
        )
    )
    merged.extend(
        _collect_source_rows(
            granted or [],
            default_reason="GrantedAI opportunity matching your focus and location.",
        )
    )
    # Drop past deadlines / closed statuses before the candidate pool is capped.
    merged = _filter_actionable_opportunities(merged)
    merged = merged[:pool]
    if ai_priority:
        return _finalize_ranked_matches(merged, context, result_limit=limit)
    return _neutral_rows(merged)[:limit]


def build_grant_agent(defaults: dict[str, Any] | None = None):
    """
    Create the agent with separate tools registered.

    Tools use profile/context defaults when args are omitted.
    System instructions are loaded from grant_agent_instructions.md.
    """
    from agents import Agent

    from services.tools import (
        build_granted_ai_tool,
        build_grants_gov_tool,
        build_usaspending_tool,
    )

    tool_defaults = dict(defaults or {})
    return Agent(
        name="Grant Matching Agent",
        instructions=load_agent_instructions(),
        tools=[
            build_grants_gov_tool(tool_defaults),
            build_usaspending_tool(tool_defaults),
            build_granted_ai_tool(tool_defaults),
        ],
        output_type=GrantMatchResult,
        model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
    )


async def run_grant_matching_agent_async(
    profile: Any,
    user_query: str = "",
    *,
    max_results: int | None = None,
) -> list[dict[str, Any]]:
    """
    Async matching: Agents SDK tools first, asyncio.gather fallback.
    Profile defaults apply unless user_query overrides specific fields.
    `max_results` caps the final ranked list (chat default 12; digests may raise it).
    """
    payload = _search_context(profile, user_query)
    limit = max(1, int(max_results or DEFAULT_RESULT_LIMIT))

    async def _fallback() -> list[dict[str, Any]]:
        sources = await fetch_all_sources_async(
            profile,
            user_query=user_query,
            context=payload,
            result_limit=limit,
        )
        return await _finalize_ranked_matches_async(
            _score_merge(
                sources["grants_gov"],
                sources["usaspending"],
                sources["granted_ai"],
                profile=payload,
                ai_priority=False,
                result_limit=limit,
            ),
            payload,
            result_limit=limit,
        )

    if not _agent_enabled():
        return await _fallback()

    try:
        from agents import Runner

        agent = build_grant_agent(payload)
        result = await Runner.run(
            agent, _matching_prompt(payload, user_query), max_turns=12
        )
        final = result.final_output

        matches: list[dict[str, Any]] = []
        if isinstance(final, GrantMatchResult):
            matches = [m.model_dump() for m in final.matches]
        elif isinstance(final, dict) and "matches" in final:
            matches = list(final.get("matches") or [])
        matches = _filter_actionable_opportunities(matches)

        if not matches:
            logger.info("Agent returned no matches; using async fallback merge")
            return await _fallback()

        matches.sort(key=lambda m: float(m.get("score") or 0), reverse=True)

        used = {m.get("source") for m in matches}
        needed = {"grants_gov", "usaspending", "granted_ai"}
        if not needed.issubset(used):
            sources = await fetch_all_sources_async(
                profile,
                user_query=user_query,
                context=payload,
                result_limit=limit,
            )
            matches = _merge_source_details(matches, sources)
            extras: list[dict[str, Any]] = []
            # Keep more extras when the caller asks for a larger digest board.
            extra_each = max(4, min(10, limit // 3 or 4))
            if sources["granted_ai"] and "granted_ai" not in used:
                extras.extend(
                    _score_merge(
                        [],
                        [],
                        sources["granted_ai"],
                        profile=payload,
                        ai_priority=False,
                        result_limit=extra_each,
                    )[:extra_each]
                )
            if sources["usaspending"] and "usaspending" not in used:
                extras.extend(
                    _score_merge(
                        [],
                        sources["usaspending"],
                        [],
                        profile=payload,
                        ai_priority=False,
                        result_limit=extra_each,
                    )[:extra_each]
                )
            if sources["grants_gov"] and "grants_gov" not in used:
                extras.extend(
                    _score_merge(
                        sources["grants_gov"],
                        [],
                        [],
                        profile=payload,
                        ai_priority=False,
                        result_limit=extra_each,
                    )[:extra_each]
                )
            if extras:
                matches.extend(extras)
                matches.sort(key=lambda m: float(m.get("score") or 0), reverse=True)

        return await _finalize_ranked_matches_async(
            matches, payload, result_limit=limit
        )
    except Exception:
        logger.exception("Grant agent failed; using async merged fallback")
        return await _fallback()


def run_grant_matching_agent(
    profile: Any,
    user_query: str = "",
    *,
    max_results: int | None = None,
) -> list[dict[str, Any]]:
    """Sync bridge for /home/matches/ JSON endpoint and weekly digests."""
    return _run_async(
        run_grant_matching_agent_async(
            profile, user_query=user_query, max_results=max_results
        )
    )
