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
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

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
        "eligibility": str(row.get("eligibility") or "")[:280],
        "amount": row.get("amount") or "",
        "award_ceiling": row.get("award_ceiling") or "",
        "award_floor": row.get("award_floor") or "",
        "deadline": row.get("deadline") or "",
        "opp_status": row.get("opp_status") or "",
        "pop_city": row.get("pop_city") or "",
        "pop_state": row.get("pop_state") or row.get("state") or "",
        "local_score": row.get("score"),
        "local_reason": row.get("reason") or "",
    }


def _apply_ai_scores(
    matches: list[dict[str, Any]],
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    AI scores take priority over local scores.
    On any failure, return the locally scored matches unchanged.
    """
    if not matches or not _ai_scoring_enabled():
        return matches

    candidates = list(matches[:18])
    payload = {
        "profile": profile,
        "grants": [_grant_score_payload(row, i) for i, row in enumerate(candidates)],
    }
    model = os.getenv("OPENAI_MODEL", "gpt-5.5").strip() or "gpt-5.5"
    system = (
        "You score grant opportunities for how likely the applicant is to win/fit. "
        "Use ONLY the provided profile and grant fields (topic/priority, location, "
        "budget, eligibility/org type, status/deadline, categories). "
        "Return JSON: {\"scores\":[{\"index\":0,\"score\":0.0,\"chance_tier\":\"high|medium|low\",\"reason\":\"...\"}]}. "
        "Include every grant index exactly once. score is 0.0-1.0. "
        "chance_tier: high>=0.75, medium>=0.55, else low. "
        "reason must be short (max 140 chars) and cite the strongest fit factors."
    )
    user = (
        "Score these grants for the applicant. Prefer semantic fit over exact keywords. "
        "local_score is only a weak hint — your score has priority.\n\n"
        f"{json.dumps(payload, ensure_ascii=True)}"
    )

    try:
        from openai import OpenAI

        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip() or None)
        response = client.chat.completions.create(
            model=model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        content = (response.choices[0].message.content or "").strip()
        data = json.loads(content)
        score_rows = data.get("scores") if isinstance(data, dict) else None
        if not isinstance(score_rows, list) or not score_rows:
            logger.warning("AI scorer returned no scores; keeping local ranking")
            return matches

        by_index: dict[int, dict[str, Any]] = {}
        for item in score_rows:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("index"))
            except (TypeError, ValueError):
                continue
            by_index[idx] = item

        if not by_index:
            return matches

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
            out["score"] = round(score, 2)
            out["chance_percent"] = int(round(score * 100))
            out["chance_tier"] = tier
            out["chance_label"] = _chance_label(tier)
            if reason:
                out["reason"] = reason
            out["score_method"] = "ai"
            rescored.append(out)

        # Keep any leftover beyond AI batch (should be rare) with local scores.
        if len(matches) > len(candidates):
            rescored.extend(matches[len(candidates) :])

        return _attach_display_fields(_rank_by_chance(rescored)[:12])
    except Exception:
        logger.exception("AI grant scoring failed; keeping local ranking")
        return matches


def _finalize_ranked_matches(
    matches: list[dict[str, Any]],
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    """Local rank first, then AI score with priority when available."""
    local = _attach_display_fields(_rank_by_chance(list(matches))[:12])
    return _apply_ai_scores(local, profile)


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


def _source_jobs(profile: Any) -> dict[str, Any]:
    """Build callable jobs for each grant source (used by parallel + streaming fetch)."""
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
                    rows=15,
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

    return {
        "grants_gov": _gov,
        "usaspending": _usa,
        "granted_ai": _granted,
    }


def fetch_all_sources(profile: Any) -> dict[str, list[dict[str, Any]]]:
    """Call all source APIs in parallel."""
    jobs = _source_jobs(profile)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fn): name for name, fn in jobs.items()}
        out: dict[str, list[dict[str, Any]]] = {
            "grants_gov": [],
            "usaspending": [],
            "granted_ai": [],
        }
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                out[name] = fut.result()
            except Exception:
                logger.warning("%s fetch crashed", name, exc_info=True)
                out[name] = []
        return out


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


def iter_grant_matching_events(profile: Any) -> Iterator[dict[str, Any]]:
    """
    Yield progressive match events for SSE streaming.
    Fetches sources in parallel and emits results as each source finishes.
    """
    payload = _profile_payload(profile)
    location = {
        "city": payload.get("location_city") or "",
        "state": payload.get("location_state") or "",
    }
    yield {
        "type": "status",
        "message": "Searching Grants.gov, USASpending, and GrantedAI in parallel…",
        "location": location,
    }

    jobs = _source_jobs(profile)
    collected: dict[str, list[dict[str, Any]]] = {
        "grants_gov": [],
        "usaspending": [],
        "granted_ai": [],
    }

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fn): name for name, fn in jobs.items()}
        for fut in as_completed(futures):
            source = futures[fut]
            label = _SOURCE_LABELS.get(source, source)
            try:
                rows = fut.result() or []
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
    if final and _ai_scoring_enabled():
        yield {
            "type": "status",
            "message": "Scoring matches with AI (topic, location, budget, eligibility)…",
            "location": location,
        }
        final = _finalize_ranked_matches(final, payload)
    high = sum(1 for m in final if m.get("chance_tier") == "high")
    medium = sum(1 for m in final if m.get("chance_tier") == "medium")
    if final:
        method = "AI" if any(m.get("score_method") == "ai" for m in final) else "local"
        done_message = (
            f"Ranked {len(final)} opportunities with {method} scoring "
            f"({high} high, {medium} medium chance)."
        )
    else:
        done_message = "No ranked matches yet for your project."
    yield {
        "type": "done",
        "message": done_message,
        "matches": final,
        "match_count": len(final),
        "location": location,
    }


def _score_source_rows(
    items: list[dict[str, Any]],
    *,
    profile: dict[str, Any] | None = None,
    default_reason: str,
) -> list[dict[str, Any]]:
    """
    Score each tool row against the user profile (topic, location, budget, etc.).
    Falls back to tool fit_score / source defaults only when profile is empty.
    """
    profile = profile or {}
    scored: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        row = _score_grant_against_profile(item, profile, rank_index=idx)
        # Prefer computed reason; keep tool match_reasons if we had nothing useful.
        if not row.get("reason") or row["reason"] == "general relevance to your project filters":
            tool_reason = row.get("match_reasons") or ""
            if tool_reason:
                row["reason"] = tool_reason
            else:
                row.setdefault("reason", default_reason)
        scored.append(row)
    return scored


def _score_merge(
    gov: list[dict[str, Any]],
    usa: list[dict[str, Any]],
    granted: list[dict[str, Any]] | None = None,
    profile: dict[str, Any] | Any | None = None,
    *,
    ai_priority: bool = True,
) -> list[dict[str, Any]]:
    """
    Merge sources, score by profile relevance, put strongest chances first.
    Local scoring always runs first; when ai_priority=True, AI scores overwrite.
    `profile` may be a Profile model or a payload dict from `_profile_payload`.
    """
    if profile is not None and not isinstance(profile, dict):
        profile_payload = _profile_payload(profile)
    else:
        profile_payload = profile or {}

    merged: list[dict[str, Any]] = []
    merged.extend(
        _score_source_rows(
            gov,
            profile=profile_payload,
            default_reason="Open opportunity from Grants.gov matching your filters.",
        )
    )
    merged.extend(
        _score_source_rows(
            usa,
            profile=profile_payload,
            default_reason="USASpending award in/near your location matching your topic.",
        )
    )
    merged.extend(
        _score_source_rows(
            granted or [],
            profile=profile_payload,
            default_reason="GrantedAI opportunity matching your focus and location.",
        )
    )
    local = _attach_display_fields(_rank_by_chance(merged)[:12])
    if ai_priority:
        return _finalize_ranked_matches(local, profile_payload)
    return local


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
    Fast path (default): parallel multi-source fetch + scored merge.
    Optional agent path: set GRANT_USE_AGENT=1 (slower; multi-turn LLM).
    """
    payload = _profile_payload(profile)

    def _fallback() -> list[dict[str, Any]]:
        sources = fetch_all_sources(profile)
        return _score_merge(
            sources["grants_gov"],
            sources["usaspending"],
            sources["granted_ai"],
            profile=payload,
            ai_priority=True,
        )

    use_agent = os.getenv("GRANT_USE_AGENT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not use_agent:
        return _fallback()

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
                matches.sort(key=lambda m: float(m.get("score") or 0), reverse=True)

        # Local rescore first, then AI scores take priority when available.
        rescored = [
            _score_grant_against_profile(m, payload, rank_index=i)
            for i, m in enumerate(matches)
        ]
        return _finalize_ranked_matches(rescored, payload)
    except Exception:
        logger.exception("Grant agent failed; using direct merged fallback")
        return _fallback()
