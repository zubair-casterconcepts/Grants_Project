"""Helpers for chat conversations and Project context sync."""

from __future__ import annotations

import logging
import os
import re
import threading
from decimal import Decimal
from typing import Any

from django.db.models import Count, QuerySet

from apps.projects.models import Project

from .models import Conversation, Message, Profile

logger = logging.getLogger(__name__)


def conversation_to_dict(conversation: Conversation) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "title": conversation.title or "New chat",
        "project_id": conversation.project_id,
        "created_at": conversation.created_at.isoformat() if conversation.created_at else "",
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else "",
    }


def conversations_with_messages(user) -> QuerySet[Conversation]:
    """Only threads that have at least one saved message (no empty drafts)."""
    return (
        Conversation.objects.filter(user=user)
        .annotate(message_count=Count("messages"))
        .filter(message_count__gt=0)
        .order_by("-updated_at", "-created_at")
    )


def message_to_dict(message: Message) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "metadata": message.metadata or {},
        "created_at": message.created_at.isoformat() if message.created_at else "",
    }


def get_or_create_active_conversation(user) -> Conversation:
    """
    Backward-compatible helper. Prefer get_latest_conversation() for the
    ChatGPT-style flow (no empty thread until the first user message).
    """
    latest = get_latest_conversation(user)
    if latest:
        return latest
    return Conversation.objects.create(user=user, title="New chat")


def get_latest_conversation(user) -> Conversation | None:
    """Latest non-empty conversation, or None if the user has no threads yet."""
    return conversations_with_messages(user).first()


def create_conversation(user, title: str = "New chat") -> Conversation:
    return Conversation.objects.create(user=user, title=(title or "New chat")[:255])


def apply_project_to_profile(project: Project, profile: Profile) -> Profile:
    """Copy linked Project fields onto Profile so matching keeps using Profile."""
    profile.title = project.title or profile.title
    profile.description = project.description or profile.description
    profile.priority_area = project.priority_area or profile.priority_area
    profile.ntee_code = project.ntee_code or ""
    profile.location_city = project.location_city or profile.location_city
    profile.location_state = project.location_state or profile.location_state
    profile.org_type = project.org_type or profile.org_type
    profile.budget_requested = project.budget_requested
    profile.eligibility_notes = project.eligibility_notes or ""
    profile.onboarding_completed = True
    profile.save()
    return profile


def upsert_project_for_conversation(
    conversation: Conversation,
    profile: Profile,
) -> Project | None:
    """
    Create/update the Conversation's Project from the current Profile.
    Matching logic stays on Profile; Project is the persisted snapshot for resume.
    """
    if not profile.onboarding_completed:
        return conversation.project

    title = (profile.title or "").strip() or "Untitled project"
    description = (profile.description or "").strip() or title
    priority = (profile.priority_area or "").strip() or Project.PriorityArea.COMMUNITY_DEVELOPMENT
    city = (profile.location_city or "").strip() or "Unknown"
    state = (profile.location_state or "").strip().upper()[:2] or "NY"
    org_type = (profile.org_type or "").strip() or Project.OrgType.OTHER
    budget = profile.budget_requested if profile.budget_requested is not None else Decimal("0")

    fields = {
        "title": title[:255],
        "description": description,
        "priority_area": priority,
        "ntee_code": profile.ntee_code or "",
        "location_city": city[:120],
        "location_state": state,
        "org_type": org_type,
        "budget_requested": budget,
        "eligibility_notes": profile.eligibility_notes or "",
    }

    if conversation.project_id:
        project = conversation.project
        for key, value in fields.items():
            setattr(project, key, value)
        project.save()
    else:
        project = Project.objects.create(user=conversation.user, **fields)
        conversation.project = project

    if conversation.title in ("", "New chat", "New Chat"):
        conversation.title = fields["title"][:255]
    conversation.save()
    return project


def _fallback_chat_title(user_query: str) -> str:
    """Rule-based short title when AI is unavailable."""
    try:
        from services.query_context import resolve_search_context

        ctx = resolve_search_context({}, user_query=user_query)
        parts: list[str] = []
        state = (ctx.get("location_state") or "").strip().upper()
        priority = (ctx.get("priority_area") or "").strip()
        if state:
            parts.append(state)
        if priority:
            parts.append(priority)
        if parts:
            return f"{' '.join(parts)} grants"[:60]
    except Exception:
        logger.debug("fallback title context parse failed", exc_info=True)

    cleaned = re.sub(
        r"\b(find|search|show|get|please|grants?|the|a|an|me|for|in|with)\b",
        " ",
        user_query or "",
        flags=re.I,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,!?:;-")
    if not cleaned:
        return "Grant search"
    words = cleaned.split()[:5]
    title = " ".join(words)
    return (title[:1].upper() + title[1:])[:60]


def generate_chat_title(user_query: str) -> str:
    """
    Short professional sidebar title for a conversation.
    Uses OpenAI when configured; otherwise a local fallback.
    """
    text = (user_query or "").strip()
    if not text:
        return "New chat"

    fallback = _fallback_chat_title(text)
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return fallback

    model = os.getenv("OPENAI_MODEL", "gpt-5.5").strip() or "gpt-5.5"
    system = (
        "You name chat threads for a grants discovery app. "
        "Return ONLY a short professional title (3 to 6 words). "
        "Title Case. No quotes, no trailing punctuation, no emoji. "
        "Capture location/topic/intent when present "
        "(e.g. 'NY Education Grants', 'California Funding Search')."
    )
    user = f"User message:\n{text[:400]}"

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        title = (response.choices[0].message.content or "").strip()
        title = title.strip(" \"'`").split("\n")[0].strip()
        title = re.sub(r"\s+", " ", title)
        if not title or len(title) < 3:
            return fallback
        return title[:60]
    except Exception:
        logger.exception("AI chat title generation failed; using fallback")
        return fallback


def _should_retitle(conversation: Conversation, first_user_text: str) -> bool:
    current = (conversation.title or "").strip()
    if current in ("", "New chat", "New Chat"):
        return True
    # Replace unprofessional titles that are just the raw first query.
    if first_user_text and current.lower() == first_user_text[: len(current)].lower():
        return True
    if len(current) > 48 and current.lower().startswith(
        (first_user_text or "").strip().lower()[:24]
    ):
        return True
    return False


def add_message(
    conversation: Conversation,
    *,
    role: str,
    content: str,
    metadata: dict | None = None,
) -> Message:
    text = (content or "").strip()
    if not text and not metadata:
        raise ValueError("empty_message")

    if role not in dict(Message.Role.choices):
        raise ValueError("invalid_role")

    message = Message.objects.create(
        conversation=conversation,
        role=role,
        content=text or (metadata.get("summary") if metadata else "") or "",
        metadata=metadata or {},
    )

    # Fast local title first; upgrade with AI in the background so the chat
    # request never blocks (and never races a conversation reload).
    if role == Message.Role.USER and text and _should_retitle(conversation, text):
        conversation.title = _fallback_chat_title(text)
        conversation.save(update_fields=["title", "updated_at"])
        conversation_id = conversation.pk
        query_text = text

        def _upgrade_title() -> None:
            try:
                title = generate_chat_title(query_text)
                Conversation.objects.filter(pk=conversation_id).update(title=title[:60])
            except Exception:
                logger.exception("Background chat title upgrade failed")

        threading.Thread(target=_upgrade_title, daemon=True).start()
    else:
        conversation.save(update_fields=["updated_at"])
    return message
