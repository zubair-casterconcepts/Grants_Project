"""
Weekly grant digest: build one payload per user and POST it to n8n.

Django owns the content (it runs the same matcher the chat UI uses and renders
the email HTML), n8n owns delivery. Every run is keyed to the Monday of the
current week, so re-running the sender never double-sends and a failed week can
be retried.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

import httpx
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from services.grant_agent import run_grant_matching_agent
from services.grant_categories import FALLBACK_CATEGORY
from services.location_utils import US_STATE_NAMES
from services.query_context import resolve_search_context

logger = logging.getLogger(__name__)

DIGEST_EVENT = "weekly_grant_digest"
PAYLOAD_VERSION = 1

SOURCE_LABELS = {
    "grants_gov": "Grants.gov",
    "usaspending": "USASpending",
    "granted_ai": "GrantedAI",
}

ORG_TYPE_LABELS = {
    "501c3": "501(c)(3)",
    "government": "Government",
    "school": "School",
    "other": "Other",
}


class DigestConfigError(RuntimeError):
    """Raised when the n8n webhook is not configured."""


def week_start_for(moment: datetime | None = None) -> date:
    """Monday (UTC) of the week that `moment` falls in."""
    now = moment or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today = now.astimezone(timezone.utc).date()
    return today - timedelta(days=today.weekday())


def is_digest_day(moment: datetime | None = None) -> bool:
    """Digests go out on Mondays (UTC)."""
    now = moment or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc).weekday() == 0


def _week_label(week_start: date) -> str:
    week_end = week_start + timedelta(days=6)
    start = f"{week_start:%b} {week_start.day}"
    end = (
        str(week_end.day)
        if week_start.month == week_end.month
        else f"{week_end:%b} {week_end.day}"
    )
    return f"{start}–{end}, {week_end.year}"


def _money(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        amount = Decimal(str(value))
    except Exception:
        return str(value)
    return f"${amount:,.0f}"


def _location_label(city: str, state: str) -> str:
    city = (city or "").strip()
    state = (state or "").strip().upper()
    if city and state:
        return f"{city}, {state}"
    if state:
        return US_STATE_NAMES.get(state, state)
    return city


def _trim(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _resolve_grant_limit(max_grants: int | None) -> int:
    """
    How many ranked grants to put in the email.

    0 (or negative) means "all matches returned by the matcher" — no second trim.
    """
    if max_grants is None:
        configured = int(getattr(settings, "WEEKLY_DIGEST_MAX_GRANTS", 0) or 0)
    else:
        configured = int(max_grants)
    return configured


def _grant_rows(matches: list[dict[str, Any]], max_grants: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    selected = matches if max_grants <= 0 else matches[:max_grants]
    for index, match in enumerate(selected, start=1):
        source = str(match.get("source") or "grants_gov")
        deadline = str(match.get("deadline") or "").strip()
        verb = "Ends" if source == "usaspending" else "Closes"
        try:
            score = round(float(match.get("score") or 0.0), 2)
        except (TypeError, ValueError):
            score = 0.0
        rows.append(
            {
                "rank": index,
                "title": str(match.get("title") or "Untitled grant"),
                "category": str(match.get("category") or "") or FALLBACK_CATEGORY,
                "agency": str(match.get("agency") or match.get("top_agency") or ""),
                "top_agency": str(match.get("top_agency") or ""),
                "agency_code": str(match.get("agency_code") or ""),
                "amount": str(match.get("amount") or ""),
                "deadline": deadline,
                "deadline_label": f"{verb} {deadline}" if deadline else "No fixed deadline",
                "opp_status": str(match.get("opp_status") or ""),
                "source": source,
                "source_label": SOURCE_LABELS.get(source, source),
                "score": score,
                "chance_percent": int(match.get("chance_percent") or round(score * 100)),
                "chance_tier": str(match.get("chance_tier") or "medium"),
                "chance_label": str(match.get("chance_label") or ""),
                "reason": _trim(match.get("reason"), 220),
                "description": _trim(match.get("description"), 320),
                "url": str(match.get("url") or ""),
                "number": str(match.get("number") or ""),
                "external_id": str(match.get("id") or match.get("number") or ""),
                "contact": {
                    "email": str(match.get("agency_email") or ""),
                    "phone": str(match.get("agency_phone") or ""),
                    "address": str(match.get("agency_address") or ""),
                },
            }
        )
    return rows


def _summary(rows: list[dict[str, Any]], profile_data: dict[str, Any]) -> dict[str, Any]:
    tiers = {"high": 0, "medium": 0, "low": 0}
    sources: dict[str, int] = {}
    categories: list[str] = []
    for row in rows:
        tier = row["chance_tier"] if row["chance_tier"] in tiers else "low"
        tiers[tier] += 1
        sources[row["source"]] = sources.get(row["source"], 0) + 1
        if row["category"] not in categories:
            categories.append(row["category"])

    focus = profile_data.get("priority_area") or profile_data.get("title") or "your project"
    place = profile_data.get("location_label") or ""
    headline = f"{len(rows)} grant match{'' if len(rows) == 1 else 'es'} for {focus}"
    if place:
        headline += f" in {place}"
    if tiers["high"]:
        headline += f" — {tiers['high']} high chance"
    return {
        "match_count": len(rows),
        "high_chance": tiers["high"],
        "medium_chance": tiers["medium"],
        "low_chance": tiers["low"],
        "top_score": rows[0]["score"] if rows else 0.0,
        "categories": categories,
        "sources": sources,
        "headline": headline,
    }


def _profile_data(profile: Any) -> dict[str, Any]:
    city = getattr(profile, "location_city", "") or ""
    state = getattr(profile, "location_state", "") or ""
    org_type = getattr(profile, "org_type", "") or ""
    budget = getattr(profile, "budget_requested", None)
    return {
        "title": getattr(profile, "title", "") or "",
        "description": getattr(profile, "description", "") or "",
        "priority_area": getattr(profile, "priority_area", "") or "",
        "location_city": city,
        "location_state": state,
        "location_label": _location_label(city, state),
        "org_type": org_type,
        "org_type_label": ORG_TYPE_LABELS.get(org_type, org_type),
        "budget_requested": str(budget) if budget is not None else "",
        "budget_label": _money(budget),
        "eligibility_notes": getattr(profile, "eligibility_notes", "") or "",
        "ntee_code": getattr(profile, "ntee_code", "") or "",
        "organization": getattr(profile, "organization", "") or "",
        "role_title": getattr(profile, "role_title", "") or "",
    }


def _links() -> dict[str, str]:
    base = settings.SITE_BASE_URL
    return {
        "app": f"{base}/home/",
        "saved": f"{base}/accounts/saved/",
        "settings": f"{base}/accounts/settings/",
        "profile": f"{base}/accounts/profile/settings/",
    }


def recipient_email(user: Any) -> str:
    return (getattr(user, "email", "") or "").strip()


def recipient_name(user: Any) -> str:
    full = (user.get_full_name() or "").strip() if hasattr(user, "get_full_name") else ""
    return full or user.get_username()


def build_digest_payload(
    profile: Any,
    *,
    week_start: date | None = None,
    max_grants: int | None = None,
    matches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Run the matcher for a profile and build the full n8n payload, including the
    rendered email HTML/text. Pass `matches` to reuse an existing match list.
    """
    user = profile.user
    week = week_start or week_start_for()
    limit = _resolve_grant_limit(max_grants)
    # Matcher chat default is 12; digests ask for every high-priority match.
    matcher_limit = limit if limit > 0 else max(
        50, int(getattr(settings, "WEEKLY_DIGEST_MATCHER_LIMIT", 50) or 50)
    )
    results = (
        matches
        if matches is not None
        else run_grant_matching_agent(profile, max_results=matcher_limit)
    )

    profile_data = _profile_data(profile)
    rows = _grant_rows(results or [], limit)
    summary = _summary(rows, profile_data)
    links = _links()
    week_label = _week_label(week)

    context = {
        "site_name": settings.SITE_NAME,
        "user_name": recipient_name(user),
        "profile": profile_data,
        "summary": summary,
        "grants": rows,
        "links": links,
        "week_label": week_label,
        "preheader": summary["headline"],
    }
    html = render_to_string("email/weekly_digest.html", context)
    text = render_to_string("email/weekly_digest.txt", context)

    subject = f"{settings.SITE_NAME}: {summary['headline']}"
    if not rows:
        subject = f"{settings.SITE_NAME}: no new grant matches this week"

    return {
        "event": DIGEST_EVENT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "week_start": week.isoformat(),
        "week_end": (week + timedelta(days=6)).isoformat(),
        "week_label": week_label,
        "user": {
            "id": user.pk,
            "username": user.get_username(),
            "email": recipient_email(user),
            "name": recipient_name(user),
            "organization": profile_data["organization"],
            "role_title": profile_data["role_title"],
        },
        "profile": profile_data,
        "search_context": resolve_search_context(profile, user_query=""),
        "summary": summary,
        "grants": rows,
        "email": {
            "to": recipient_email(user),
            "to_name": recipient_name(user),
            "reply_to": settings.WEEKLY_DIGEST_REPLY_TO,
            "subject": subject,
            "preheader": summary["headline"],
            "html": html,
            "text": text.strip() or strip_tags(html),
        },
        "links": links,
        "meta": {
            "source": "grants-django",
            "payload_version": PAYLOAD_VERSION,
            "digest_id": f"{user.get_username()}-{week.isoformat()}",
        },
    }


def webhook_configured() -> bool:
    return bool(settings.N8N_WEEKLY_WEBHOOK_URL)


def post_digest(payload: dict[str, Any]) -> tuple[int, str]:
    """POST one digest payload to the n8n webhook. Returns (status, body snippet)."""
    url = settings.N8N_WEEKLY_WEBHOOK_URL
    if not url:
        raise DigestConfigError(
            "N8N_WEEKLY_GRANTED_WEBHOOK_URL is not set; cannot send weekly digests."
        )
    headers = {"Content-Type": "application/json"}
    if settings.N8N_WEBHOOK_AUTH_HEADER_VALUE:
        headers[settings.N8N_WEBHOOK_AUTH_HEADER] = settings.N8N_WEBHOOK_AUTH_HEADER_VALUE

    response = httpx.post(
        url,
        json=payload,
        headers=headers,
        timeout=settings.N8N_WEBHOOK_TIMEOUT_SECONDS,
    )
    return response.status_code, (response.text or "")[:500]


def eligible_profiles(usernames: Iterable[str] | None = None):
    """Onboarded users who opted in and have an email address to send to."""
    from apps.accounts.models import Profile

    queryset = (
        Profile.objects.select_related("user")
        .filter(
            onboarding_completed=True,
            weekly_digest_enabled=True,
            user__is_active=True,
        )
        .order_by("user__username")
    )
    names = [name for name in (usernames or []) if name]
    if names:
        queryset = queryset.filter(user__username__in=names)
    return queryset


def send_digest_for_profile(
    profile: Any,
    *,
    week_start: date | None = None,
    force: bool = False,
    dry_run: bool = False,
    max_grants: int | None = None,
) -> dict[str, Any]:
    """
    Build and deliver one user's digest, recording the outcome. Returns a result
    dict with `status` in {sent, skipped, failed} and the payload when built.
    """
    from apps.accounts.models import WeeklyDigestLog

    user = profile.user
    week = week_start or week_start_for()
    username = user.get_username()
    email = recipient_email(user)

    def _record(status: str, detail: str = "", **extra: Any) -> dict[str, Any]:
        if not dry_run:
            WeeklyDigestLog.objects.update_or_create(
                user=user,
                week_start=week,
                defaults={
                    "status": status,
                    "email": email[:254],
                    "detail": detail[:2000],
                    "match_count": extra.get("match_count") or 0,
                    "webhook_status": extra.get("webhook_status"),
                },
            )
        return {
            "username": username,
            "email": email,
            "week_start": week.isoformat(),
            "status": status,
            "detail": detail,
            **extra,
        }

    # Checked before anything else so a resend never overwrites a "sent" row.
    already_sent = WeeklyDigestLog.objects.filter(
        user=user, week_start=week, status=WeeklyDigestLog.Status.SENT
    ).exists()
    if already_sent and not force:
        return {
            "username": username,
            "email": email,
            "week_start": week.isoformat(),
            "status": "skipped",
            "detail": "Digest already sent for this week (use --force to resend).",
        }

    if not email:
        return _record("skipped", "No email address on the user account.")

    try:
        payload = build_digest_payload(profile, week_start=week, max_grants=max_grants)
    except Exception as exc:
        logger.exception("Weekly digest build failed for %s", username)
        return _record("failed", f"Could not build digest: {exc}")

    match_count = payload["summary"]["match_count"]
    if dry_run:
        result = _record("skipped", "Dry run — webhook not called.", match_count=match_count)
        result["payload"] = payload
        return result

    try:
        status_code, body = post_digest(payload)
    except DigestConfigError:
        raise
    except Exception as exc:
        logger.exception("Weekly digest webhook failed for %s", username)
        return _record("failed", f"Webhook error: {exc}", match_count=match_count)

    if 200 <= status_code < 300:
        result = _record(
            "sent",
            f"Webhook accepted ({status_code}).",
            match_count=match_count,
            webhook_status=status_code,
        )
    else:
        result = _record(
            "failed",
            f"Webhook returned {status_code}: {body}",
            match_count=match_count,
            webhook_status=status_code,
        )
    result["payload"] = payload
    return result


def send_weekly_digests(
    *,
    usernames: Iterable[str] | None = None,
    force: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
    max_grants: int | None = None,
    require_digest_day: bool = True,
) -> dict[str, Any]:
    """Run the digest for every eligible user. Safe to call more than once a week."""
    week = week_start_for()
    if require_digest_day and not is_digest_day():
        return {
            "week_start": week.isoformat(),
            "skipped_reason": "not_digest_day",
            "detail": "Weekly digests are sent on Mondays (UTC).",
            "sent": 0,
            "skipped": 0,
            "failed": 0,
            "results": [],
        }

    if not dry_run and not webhook_configured():
        raise DigestConfigError(
            "N8N_WEEKLY_GRANTED_WEBHOOK_URL is not set; cannot send weekly digests."
        )

    profiles = list(eligible_profiles(usernames)[: limit or None])
    results = [
        send_digest_for_profile(
            profile,
            week_start=week,
            force=force,
            dry_run=dry_run,
            max_grants=max_grants,
        )
        for profile in profiles
    ]
    return {
        "week_start": week.isoformat(),
        "considered": len(profiles),
        "sent": sum(1 for r in results if r["status"] == "sent"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "failed": sum(1 for r in results if r["status"] == "failed"),
        "results": results,
    }
