"""
OpenAI Agents SDK grant matcher.

Separate tools are registered on the agent:
  - grants_gov
  - usaspending
  - granted_ai

The agent chooses which sources to call, then ranks/keeps grants by
topic, category, location, and budget. A direct multi-source merge remains
as a safety fallback so the dashboard never breaks.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from services.location_utils import location_from_profile

logger = logging.getLogger(__name__)

_INSTRUCTIONS_PATH = Path(__file__).with_name("grant_agent_instructions.md")

# Kept as a hard fallback so a missing instructions file never breaks matching.
_DEFAULT_AGENT_INSTRUCTIONS = """# Grant Matching Agent

You are the Grants matching agent. Identify the strongest funding opportunities by querying approved sources, then rank against the user profile.

## Operating flow

1. Review the profile (topic, priority area, location, budget, org type).
2. Call grants_gov, usaspending, and granted_ai. Prefer all three sources.
3. Pass keyword, priority_area, location_city, and location_state on every tool call.
4. For granted_ai, also pass org_type from the profile when available.
5. Keep relevant opportunities and rank by topic, category, location, then budget.
6. Score each grant 0.0-1.0, set chance_percent to round(score * 100), and add a short reason.
7. Preserve provider fields from tools (agency, agency_address, contacts, amounts, dates).
8. Return structured matches with source grants_gov, usaspending, or granted_ai.

## Rules

- Do not invent opportunities, agencies, addresses, amounts, deadlines, or URLs.
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


def _profile_payload(profile: Any) -> dict[str, Any]:
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


def _compact(items: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    from services.tools._normalize import compact_source_rows

    return compact_source_rows(items, source=source)


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
        prepared.append(row)
    return prepared


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


def fetch_all_sources(profile: Any) -> dict[str, list[dict[str, Any]]]:
    """Safety fallback: call all source APIs directly (used if the agent fails)."""
    from services.granted_ai import search_grants
    from services.grants_gov import search_with_filters
    from services.usaspending import search_awards

    payload = _profile_payload(profile)
    keyword = payload["title"] or " ".join(payload["description"].split()[:8]) or "grant"
    city = payload["location_city"]
    state = payload["location_state"]
    priority = payload["priority_area"]
    org_type = payload["org_type"]

    def _gov():
        try:
            return _compact(
                search_with_filters(
                    keyword=keyword,
                    priority_area=priority,
                    location_city=city,
                    location_state=state,
                    rows=25,
                ),
                "grants_gov",
            )
        except Exception:
            logger.warning("grants_gov fallback fetch failed", exc_info=True)
            return []

    def _usa():
        try:
            return _compact(
                search_awards(
                    keyword=keyword,
                    priority_area=priority,
                    location_city=city,
                    location_state=state,
                    limit=10,
                ),
                "usaspending",
            )
        except Exception:
            logger.warning("usaspending fallback fetch failed", exc_info=True)
            return []

    def _granted():
        try:
            return _compact(
                search_grants(
                    keyword=keyword,
                    priority_area=priority,
                    location_city=city,
                    location_state=state,
                    org_type=org_type,
                    limit=10,
                ),
                "granted_ai",
            )
        except Exception:
            logger.warning("granted_ai fallback fetch failed", exc_info=True)
            return []

    with ThreadPoolExecutor(max_workers=3) as pool:
        gov_f = pool.submit(_gov)
        usa_f = pool.submit(_usa)
        granted_f = pool.submit(_granted)
        return {
            "grants_gov": gov_f.result(),
            "usaspending": usa_f.result(),
            "granted_ai": granted_f.result(),
        }


def _score_source_rows(
    items: list[dict[str, Any]],
    *,
    base: float,
    floor: float,
    step: float,
    default_reason: str,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        row = dict(item)
        fit = row.get("fit_score")
        if fit not in (None, ""):
            try:
                row["score"] = round(max(floor, min(1.0, float(fit))), 2)
            except (TypeError, ValueError):
                row["score"] = round(max(floor, base - idx * step), 2)
        else:
            row["score"] = round(max(floor, base - idx * step), 2)
        reason = row.get("match_reasons") or row.get("reason") or default_reason
        row.setdefault("reason", reason)
        row.setdefault("amount", "")
        scored.append(row)
    return scored


def _score_merge(
    gov: list[dict[str, Any]],
    usa: list[dict[str, Any]],
    granted: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    merged.extend(
        _score_source_rows(
            gov,
            base=0.93,
            floor=0.55,
            step=0.03,
            default_reason="Open opportunity from Grants.gov matching your filters.",
        )
    )
    merged.extend(
        _score_source_rows(
            usa,
            base=0.90,
            floor=0.50,
            step=0.03,
            default_reason="USASpending award in/near your location matching your topic.",
        )
    )
    merged.extend(
        _score_source_rows(
            granted or [],
            base=0.91,
            floor=0.52,
            step=0.03,
            default_reason="GrantedAI opportunity matching your focus and location.",
        )
    )
    merged.sort(key=lambda m: float(m.get("score") or 0), reverse=True)
    return _attach_display_fields(merged[:12])


def build_grant_agent():
    """
    Create the agent with separate tools registered.

    Tools:
      - grants_gov
      - usaspending
      - granted_ai

    System instructions are loaded from grant_agent_instructions.md.
    """
    from agents import Agent

    from services.tools import (
        build_granted_ai_tool,
        build_grants_gov_tool,
        build_usaspending_tool,
    )

    return Agent(
        name="Grant Matching Agent",
        instructions=load_agent_instructions(),
        tools=[
            build_grants_gov_tool(),
            build_usaspending_tool(),
            build_granted_ai_tool(),
        ],
        output_type=GrantMatchResult,
        model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
    )


def run_grant_matching_agent(profile: Any) -> list[dict[str, Any]]:
    """
    Primary: agent selects registered tools, fetches sources, ranks/keeps grants.
    Fallback: direct multi-source merge if no API key or agent fails.
    """
    payload = _profile_payload(profile)

    def _fallback() -> list[dict[str, Any]]:
        sources = fetch_all_sources(profile)
        return _score_merge(
            sources["grants_gov"],
            sources["usaspending"],
            sources["granted_ai"],
        )

    if not os.getenv("OPENAI_API_KEY", "").strip():
        logger.warning("OPENAI_API_KEY missing; using direct merged fallback")
        return _fallback()

    prompt = (
        "Run the matching flow for this user profile. "
        "Call the source tools (prefer grants_gov, usaspending, and granted_ai), "
        "keep the best grants, score them, and return structured matches. "
        "Preserve agency name, agency_address, and other provider fields. "
        "Set chance_percent to round(score * 100).\n\n"
        f"USER_PROFILE_JSON:\n{json.dumps(payload, indent=2)}"
    )

    try:
        from agents import Runner

        agent = build_grant_agent()
        result = Runner.run_sync(agent, prompt, max_turns=12)
        final = result.final_output

        matches: list[dict[str, Any]] = []
        if isinstance(final, GrantMatchResult):
            matches = [m.model_dump() for m in final.matches]
        elif isinstance(final, dict) and "matches" in final:
            matches = list(final.get("matches") or [])

        if not matches:
            logger.info("Agent returned no matches; using fallback merge")
            return _fallback()

        matches.sort(key=lambda m: float(m.get("score") or 0), reverse=True)

        # If agent omitted a source, top up from a direct fetch of that source.
        used = {m.get("source") for m in matches}
        needed = {"grants_gov", "usaspending", "granted_ai"}
        if not needed.issubset(used):
            sources = fetch_all_sources(profile)
            matches = _merge_source_details(matches, sources)
            extras: list[dict[str, Any]] = []
            if sources["granted_ai"] and "granted_ai" not in used:
                extras.extend(_score_merge([], [], sources["granted_ai"])[:4])
            if sources["usaspending"] and "usaspending" not in used:
                extras.extend(_score_merge([], sources["usaspending"], [])[:4])
            if sources["grants_gov"] and "grants_gov" not in used:
                extras.extend(_score_merge(sources["grants_gov"], [], [])[:4])
            if extras:
                matches.extend(extras)
                matches.sort(key=lambda m: float(m.get("score") or 0), reverse=True)

        return _attach_display_fields(matches[:12])
    except Exception:
        logger.exception("Grant agent failed; using direct merged fallback")
        return _fallback()
