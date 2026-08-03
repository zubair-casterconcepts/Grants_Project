"""Helpers for chat conversations and Project context sync."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from apps.projects.models import Project

from .models import Conversation, Message, Profile


def conversation_to_dict(conversation: Conversation) -> dict[str, Any]:
    return {
        "id": conversation.id,
        "title": conversation.title or "New chat",
        "project_id": conversation.project_id,
        "created_at": conversation.created_at.isoformat() if conversation.created_at else "",
        "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else "",
    }


def message_to_dict(message: Message) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "metadata": message.metadata or {},
        "created_at": message.created_at.isoformat() if message.created_at else "",
    }


def get_or_create_active_conversation(user) -> Conversation:
    latest = (
        Conversation.objects.filter(user=user)
        .order_by("-updated_at", "-created_at")
        .first()
    )
    if latest:
        return latest
    return Conversation.objects.create(user=user, title="New chat")


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

    # Title from first user message when still default.
    if (
        role == Message.Role.USER
        and conversation.title in ("New chat", "New Chat", "")
        and text
    ):
        conversation.title = text[:80]
    conversation.save(update_fields=["title", "updated_at"])
    return message
