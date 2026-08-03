import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from apps.accounts.chat_services import (
    add_message,
    apply_project_to_profile,
    conversation_to_dict,
    create_conversation,
    get_or_create_active_conversation,
    message_to_dict,
    upsert_project_for_conversation,
)
from apps.accounts.forms import ProfileIntakeForm, STATE_CHOICES
from apps.accounts.models import Conversation, Profile, SavedGrant
from apps.accounts.services import get_or_create_profile
from services.grant_agent import iter_grant_matching_events, run_grant_matching_agent
from services.location_utils import normalize_location

from .services import attempt_login, attempt_logout


def _prepare_matches(user, matches):
    saved_keys = {
        f"{row.source}:{row.external_id}"
        for row in SavedGrant.objects.filter(user=user).only("source", "external_id")
    }
    prepared = []
    for match in matches:
        row = dict(match)
        key = (
            f"{row.get('source', '')}:"
            f"{row.get('id') or row.get('number') or row.get('url') or row.get('title', '')}"
        )
        row["is_saved"] = key in saved_keys
        row["save_external_id"] = (
            row.get("id")
            or row.get("number")
            or row.get("url")
            or row.get("title", "")
        )
        prepared.append(row)
    return prepared, len(saved_keys)


def _chat_bootstrap(profile: Profile) -> dict:
    priority_choices = [
        {"value": value, "label": label}
        for value, label in Profile.PriorityArea.choices
    ]
    org_choices = [
        {"value": value, "label": label}
        for value, label in Profile.OrgType.choices
    ]
    state_choices = [
        {"value": value, "label": label}
        for value, label in STATE_CHOICES
        if value
    ]
    return {
        "onboarding_completed": profile.onboarding_completed,
        "username": profile.user.get_username(),
        "profile": {
            "organization": profile.organization or "",
            "role_title": profile.role_title or "",
            "title": profile.title or "",
            "description": profile.description or "",
            "priority_area": profile.priority_area or "",
            "location_city": profile.location_city or "",
            "location_state": profile.location_state or "",
            "org_type": profile.org_type or "",
            "budget_requested": (
                str(profile.budget_requested)
                if profile.budget_requested is not None
                else ""
            ),
            "eligibility_notes": profile.eligibility_notes or "",
        },
        "choices": {
            "priority_area": priority_choices,
            "org_type": org_choices,
            "location_state": state_choices,
        },
    }


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("auth:home")

    if request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        user = attempt_login(request, username, password)
        if user is not None:
            get_or_create_profile(user)
            next_url = request.POST.get("next") or request.GET.get("next")
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect("auth:home")
        messages.error(request, "Invalid username or password.")

    return render(
        request,
        "login.html",
        {"next": request.GET.get("next", "")},
    )


@require_http_methods(["GET", "POST"])
def logout_view(request):
    """End the session and send the user to login (GET allowed as a reliable fallback)."""
    was_authenticated = request.user.is_authenticated
    if was_authenticated:
        attempt_logout(request)
    # Message is stored on the fresh post-logout session.
    if was_authenticated:
        messages.success(request, "You have been signed out.")
    response = redirect("auth:login")
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    return response


@login_required
def home_view(request):
    """ChatGPT-style chat shell with conversation sidebar."""
    profile = get_or_create_profile(request.user)
    saved_count = SavedGrant.objects.filter(user=request.user).count()
    active = get_or_create_active_conversation(request.user)
    conversations = [
        conversation_to_dict(row)
        for row in Conversation.objects.filter(user=request.user).order_by(
            "-updated_at", "-created_at"
        )[:100]
    ]
    return render(
        request,
        "home.html",
        {
            "profile": profile,
            "saved_count": saved_count,
            "matches_api_url": "/home/matches/",
            "matches_stream_url": "/home/matches/stream/",
            "chat_profile_url": "/home/chat/profile/",
            "conversations_url": "/home/conversations/",
            "chat_bootstrap": _chat_bootstrap(profile),
            "active_conversation_id": active.id,
            "conversations_bootstrap": {
                "active_id": active.id,
                "conversations": conversations,
            },
        },
    )


@login_required
@require_GET
def matches_api_view(request):
    """JSON endpoint used by the chat grants loader."""
    profile = get_or_create_profile(request.user)
    if profile.needs_onboarding:
        return JsonResponse({"error": "onboarding_required"}, status=403)

    try:
        matches = run_grant_matching_agent(profile)
        prepared, saved_count = _prepare_matches(request.user, matches)
        return JsonResponse(
            {
                "matches": prepared,
                "match_count": len(prepared),
                "saved_count": saved_count,
                "location": {
                    "city": profile.location_city or "",
                    "state": profile.location_state or "",
                },
            }
        )
    except Exception:
        return JsonResponse(
            {
                "matches": [],
                "match_count": 0,
                "saved_count": SavedGrant.objects.filter(user=request.user).count(),
                "error": "match_failed",
            },
            status=500,
        )


@login_required
@require_GET
def matches_stream_api_view(request):
    """SSE stream: status + per-source matches as each API finishes."""
    profile = get_or_create_profile(request.user)
    if profile.needs_onboarding:
        return JsonResponse({"error": "onboarding_required"}, status=403)

    def event_stream():
        try:
            for event in iter_grant_matching_events(profile):
                payload = dict(event)
                if "matches" in payload:
                    prepared, saved_count = _prepare_matches(
                        request.user, payload.get("matches") or []
                    )
                    payload["matches"] = prepared
                    payload["saved_count"] = saved_count
                    payload["match_count"] = len(prepared)
                yield f"data: {json.dumps(payload)}\n\n"
        except Exception:
            fail = {
                "type": "error",
                "message": "Something went wrong while ranking grants.",
                "matches": [],
                "match_count": 0,
                "saved_count": SavedGrant.objects.filter(user=request.user).count(),
            }
            yield f"data: {json.dumps(fail)}\n\n"

    response = StreamingHttpResponse(
        event_stream(),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache, no-transform"
    response["X-Accel-Buffering"] = "no"
    return response


@login_required
@require_POST
def chat_profile_api(request):
    """
    Persist chat intake fields onto Profile.
    Matching logic is unchanged — this only updates the profile the agent already uses.
    """
    profile = get_or_create_profile(request.user)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    fields = payload.get("fields") or {}
    if not isinstance(fields, dict):
        return JsonResponse({"ok": False, "error": "invalid_fields"}, status=400)

    allowed = {
        "organization",
        "role_title",
        "title",
        "description",
        "priority_area",
        "location_city",
        "location_state",
        "org_type",
        "budget_requested",
        "eligibility_notes",
    }
    updates = {key: fields[key] for key in fields if key in allowed}

    if "location_city" in updates or "location_state" in updates:
        city = updates.get("location_city", profile.location_city or "")
        state = updates.get("location_state", profile.location_state or "")
        city, state = normalize_location(city, state)
        updates["location_city"] = city
        updates["location_state"] = state

    if "budget_requested" in updates:
        raw = updates["budget_requested"]
        if raw in ("", None):
            updates["budget_requested"] = None
        else:
            try:
                updates["budget_requested"] = str(raw).replace(",", "").replace("$", "").strip()
            except Exception:
                return JsonResponse(
                    {"ok": False, "error": "invalid_budget", "field": "budget_requested"},
                    status=400,
                )

    if "priority_area" in updates and updates["priority_area"]:
        if updates["priority_area"] not in dict(Profile.PriorityArea.choices):
            return JsonResponse(
                {"ok": False, "error": "invalid_choice", "field": "priority_area"},
                status=400,
            )

    if "org_type" in updates and updates["org_type"]:
        if updates["org_type"] not in dict(Profile.OrgType.choices):
            return JsonResponse(
                {"ok": False, "error": "invalid_choice", "field": "org_type"},
                status=400,
            )

    complete = bool(payload.get("complete"))
    if complete:
        data = {
            "organization": updates.get("organization", profile.organization),
            "role_title": updates.get("role_title", profile.role_title),
            "title": updates.get("title", profile.title),
            "description": updates.get("description", profile.description),
            "priority_area": updates.get("priority_area", profile.priority_area),
            "location_city": updates.get("location_city", profile.location_city),
            "location_state": updates.get("location_state", profile.location_state),
            "org_type": updates.get("org_type", profile.org_type),
            "budget_requested": updates.get(
                "budget_requested",
                profile.budget_requested if profile.budget_requested is not None else "",
            ),
            "eligibility_notes": updates.get(
                "eligibility_notes", profile.eligibility_notes
            ),
        }
        form = ProfileIntakeForm(data, instance=profile)
        if not form.is_valid():
            return JsonResponse(
                {"ok": False, "error": "validation_failed", "errors": form.errors},
                status=400,
            )
        profile = form.save(commit=False)
        profile.ntee_code = profile.ntee_code or ""
        profile.onboarding_completed = True
        profile.save()
    elif updates:
        from decimal import Decimal, InvalidOperation

        if "location_state" in updates and updates["location_state"]:
            valid_states = {code for code, _ in STATE_CHOICES if code}
            if updates["location_state"] not in valid_states:
                return JsonResponse(
                    {"ok": False, "error": "invalid_choice", "field": "location_state"},
                    status=400,
                )

        if "budget_requested" in updates:
            raw = updates["budget_requested"]
            if raw in ("", None):
                profile.budget_requested = None
            else:
                try:
                    profile.budget_requested = Decimal(str(raw))
                except (InvalidOperation, ValueError):
                    return JsonResponse(
                        {"ok": False, "error": "invalid_budget", "field": "budget_requested"},
                        status=400,
                    )
            updates.pop("budget_requested", None)

        for key, value in updates.items():
            setattr(profile, key, value if value is not None else "")
        profile.save()

    return JsonResponse({"ok": True, "bootstrap": _chat_bootstrap(profile)})


def _user_conversation(user, conversation_id: int) -> Conversation:
    return get_object_or_404(Conversation, pk=conversation_id, user=user)


@login_required
@require_http_methods(["GET", "POST"])
def conversations_api_view(request):
    """List conversations (newest first) or create a new empty chat."""
    if request.method == "GET":
        rows = Conversation.objects.filter(user=request.user).order_by(
            "-updated_at", "-created_at"
        )[:100]
        return JsonResponse(
            {
                "ok": True,
                "conversations": [conversation_to_dict(row) for row in rows],
            }
        )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        payload = {}
    title = (payload.get("title") or "New chat").strip() or "New chat"
    conversation = create_conversation(request.user, title=title)
    return JsonResponse(
        {"ok": True, "conversation": conversation_to_dict(conversation)},
        status=201,
    )


@login_required
@require_http_methods(["GET", "DELETE"])
def conversation_detail_api(request, conversation_id: int):
    """Load one conversation, or delete it (and its messages)."""
    conversation = _user_conversation(request.user, conversation_id)

    if request.method == "DELETE":
        deleted_id = conversation.id
        conversation.delete()
        remaining = [
            conversation_to_dict(row)
            for row in Conversation.objects.filter(user=request.user).order_by(
                "-updated_at", "-created_at"
            )[:100]
        ]
        next_id = remaining[0]["id"] if remaining else None
        return JsonResponse(
            {
                "ok": True,
                "deleted_id": deleted_id,
                "conversations": remaining,
                "next_id": next_id,
            }
        )

    profile = get_or_create_profile(request.user)
    if conversation.project_id:
        apply_project_to_profile(conversation.project, profile)
        profile.refresh_from_db()

    messages = [
        message_to_dict(row)
        for row in conversation.messages.order_by("created_at", "id")
    ]
    return JsonResponse(
        {
            "ok": True,
            "conversation": conversation_to_dict(conversation),
            "messages": messages,
            "bootstrap": _chat_bootstrap(profile),
            "project_id": conversation.project_id,
        }
    )


@login_required
@require_POST
def conversation_messages_api(request, conversation_id: int):
    """Persist a user/assistant message into the conversation."""
    conversation = _user_conversation(request.user, conversation_id)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    role = (payload.get("role") or "").strip()
    content = payload.get("content") or ""
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    try:
        message = add_message(
            conversation,
            role=role,
            content=content,
            metadata=metadata,
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    conversation.refresh_from_db()
    return JsonResponse(
        {
            "ok": True,
            "message": message_to_dict(message),
            "conversation": conversation_to_dict(conversation),
        }
    )


@login_required
@require_POST
def conversation_sync_project_api(request, conversation_id: int):
    """Snapshot current Profile onto the Conversation's linked Project."""
    conversation = _user_conversation(request.user, conversation_id)
    profile = get_or_create_profile(request.user)
    project = upsert_project_for_conversation(conversation, profile)
    conversation.refresh_from_db()
    return JsonResponse(
        {
            "ok": True,
            "conversation": conversation_to_dict(conversation),
            "project_id": project.id if project else None,
            "bootstrap": _chat_bootstrap(profile),
        }
    )
