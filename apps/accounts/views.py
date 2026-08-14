import logging
import threading

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from services.weekly_digest import (
    DigestConfigError,
    is_digest_day,
    send_weekly_digests,
    week_start_for,
)

from .forms import (
    PasswordUpdateForm,
    ProfileAccountForm,
    ProfileIntakeForm,
    StarterPromptForm,
)
from .models import SavedGrant, StarterPrompt
from .chat_services import sync_profile_to_user_projects
from .services import get_or_create_profile

logger = logging.getLogger(__name__)


@login_required
@require_http_methods(["GET", "POST"])
def onboarding_view(request):
    """Legacy form route — intake now happens in the chat UI on /home/."""
    return redirect("auth:home")


@login_required
@require_http_methods(["GET", "POST"])
def settings_view(request):
    """Project settings used for grant matching (topic, location, budget, etc.)."""
    profile = get_or_create_profile(request.user)
    if profile.needs_onboarding:
        return redirect("auth:home")

    if request.method == "POST":
        form = ProfileIntakeForm(request.POST, instance=profile)
        if form.is_valid():
            profile = form.save(commit=False)
            profile.ntee_code = profile.ntee_code or ""
            profile.onboarding_completed = True
            profile.save()
            # Keep linked Project rows aligned so old chats cannot revive stale location.
            sync_profile_to_user_projects(profile)
            messages.success(request, "Settings updated.")
            return redirect("accounts:settings")
    else:
        form = ProfileIntakeForm(instance=profile)

    return render(
        request,
        "accounts/settings.html",
        {
            "form": form,
            "profile": profile,
            "saved_count": SavedGrant.objects.filter(user=request.user).count(),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def profile_settings_view(request):
    """Profile settings — organization/role at top; password form below."""
    profile = get_or_create_profile(request.user)
    if profile.needs_onboarding:
        return redirect("auth:home")

    password_form = PasswordUpdateForm(user=request.user)
    if request.method == "POST":
        account_form = ProfileAccountForm(request.POST, instance=profile)
        if account_form.is_valid():
            account_form.save()
            messages.success(request, "Profile updated.")
            return redirect("accounts:profile_settings")
    else:
        account_form = ProfileAccountForm(instance=profile)

    return render(
        request,
        "accounts/profile_settings.html",
        {
            "profile": profile,
            "account_form": account_form,
            "password_form": password_form,
            "saved_count": SavedGrant.objects.filter(user=request.user).count(),
        },
    )


@login_required
@require_POST
def change_password_view(request):
    """Verify current password and set a new one; keep the user signed in."""
    profile = get_or_create_profile(request.user)
    if profile.needs_onboarding:
        return redirect("auth:home")

    password_form = PasswordUpdateForm(user=request.user, data=request.POST)
    if password_form.is_valid():
        user = password_form.save()
        update_session_auth_hash(request, user)
        messages.success(request, "Password updated successfully.")
        return redirect("accounts:profile_settings")

    account_form = ProfileAccountForm(instance=profile)
    messages.error(request, "Could not update password. Please check the fields below.")
    return render(
        request,
        "accounts/profile_settings.html",
        {
            "profile": profile,
            "account_form": account_form,
            "password_form": password_form,
            "saved_count": SavedGrant.objects.filter(user=request.user).count(),
        },
        status=400,
    )


@login_required
def profile_view(request):
    return redirect("accounts:profile_settings")


def _starter_prompt_context(request, *, create_form=None, error_pk=None, error_form=None):
    """Build the list of (prompt, bound-form) rows plus the add form."""
    rows = []
    for prompt in StarterPrompt.objects.all().order_by("position", "id"):
        if error_pk is not None and prompt.pk == error_pk and error_form is not None:
            rows.append((prompt, error_form))
        else:
            rows.append((prompt, StarterPromptForm(instance=prompt)))
    return {
        "rows": rows,
        "create_form": create_form or StarterPromptForm(),
        "open_create": create_form is not None,
        "saved_count": SavedGrant.objects.filter(user=request.user).count(),
    }


@login_required
@require_http_methods(["GET", "POST"])
def starter_prompts_view(request):
    """Manage the customizable chat starter cards ("instructions"). Staff only."""
    profile = get_or_create_profile(request.user)
    if profile.needs_onboarding:
        return redirect("auth:home")
    if not request.user.is_staff:
        messages.error(request, "You don't have access to chat instructions.")
        return redirect("auth:home")

    if request.method == "POST":
        op = (request.POST.get("op") or "").strip()

        if op == "delete":
            deleted, _ = StarterPrompt.objects.filter(pk=request.POST.get("id")).delete()
            messages.success(
                request,
                "Instruction removed." if deleted else "That instruction no longer exists.",
            )
            return redirect("accounts:starter_prompts")

        if op == "create":
            form = StarterPromptForm(request.POST)
            if form.is_valid():
                form.save()
                messages.success(request, "Instruction added.")
                return redirect("accounts:starter_prompts")
            messages.error(request, "Please fix the errors in the new instruction.")
            return render(
                request,
                "accounts/starter_prompts.html",
                _starter_prompt_context(request, create_form=form),
                status=400,
            )

        if op == "update":
            instance = get_object_or_404(StarterPrompt, pk=request.POST.get("id"))
            form = StarterPromptForm(request.POST, instance=instance)
            if form.is_valid():
                form.save()
                messages.success(request, "Instruction updated.")
                return redirect("accounts:starter_prompts")
            messages.error(request, f"Please fix the errors in “{instance.title}”.")
            return render(
                request,
                "accounts/starter_prompts.html",
                _starter_prompt_context(request, error_pk=instance.pk, error_form=form),
                status=400,
            )

        messages.error(request, "Unknown action.")
        return redirect("accounts:starter_prompts")

    return render(
        request,
        "accounts/starter_prompts.html",
        _starter_prompt_context(request),
    )


@login_required
@require_http_methods(["GET"])
def saved_grants_view(request):
    profile = get_or_create_profile(request.user)
    if profile.needs_onboarding:
        return redirect("auth:home")

    saved = SavedGrant.objects.filter(user=request.user)
    return render(
        request,
        "accounts/saved_grants.html",
        {
            "profile": profile,
            "saved_grants": saved,
            "saved_count": saved.count(),
        },
    )


def _wants_json(request) -> bool:
    accept = (request.headers.get("Accept") or "").lower()
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in accept
    )


@login_required
@require_POST
def save_grant_view(request):
    profile = get_or_create_profile(request.user)
    if profile.needs_onboarding:
        if _wants_json(request):
            return JsonResponse({"ok": False, "error": "onboarding_required"}, status=403)
        return redirect("auth:home")

    source = (request.POST.get("source") or "").strip()
    external_id = (request.POST.get("external_id") or request.POST.get("id") or "").strip()
    title = (request.POST.get("title") or "").strip()
    url = (request.POST.get("url") or "").strip()
    wants_json = _wants_json(request)

    if source not in dict(SavedGrant.Source.choices):
        if wants_json:
            return JsonResponse({"ok": False, "error": "unknown_source"}, status=400)
        messages.error(request, "Could not save: unknown grant source.")
        return redirect("auth:home")
    if not title:
        if wants_json:
            return JsonResponse({"ok": False, "error": "missing_title"}, status=400)
        messages.error(request, "Could not save: missing grant title.")
        return redirect("auth:home")
    if not external_id:
        # Fall back to URL or title so uniqueness still works.
        external_id = url or title[:240]

    score_raw = request.POST.get("score") or ""
    try:
        score = float(score_raw) if score_raw != "" else None
    except ValueError:
        score = None

    _, created = SavedGrant.objects.update_or_create(
        user=request.user,
        source=source,
        external_id=external_id[:255],
        defaults={
            "title": title[:500],
            "agency": (request.POST.get("agency") or "")[:255],
            "agency_code": (request.POST.get("agency_code") or "")[:64],
            "agency_address": (request.POST.get("agency_address") or "")[:500],
            "top_agency": (request.POST.get("top_agency") or "")[:255],
            "deadline": (request.POST.get("deadline") or "")[:64],
            "url": url[:1000],
            "opp_status": (request.POST.get("opp_status") or "")[:120],
            "number": (request.POST.get("number") or "")[:120],
            "amount": (request.POST.get("amount") or "")[:64],
            "category": (request.POST.get("category") or "")[:64],
            "score": score,
            "reason": request.POST.get("reason") or "",
            "description": request.POST.get("description") or "",
        },
    )
    if wants_json:
        return JsonResponse(
            {
                "ok": True,
                "created": created,
                "saved_count": SavedGrant.objects.filter(user=request.user).count(),
            }
        )
    if created:
        messages.success(request, "Grant saved.")
    else:
        messages.success(request, "Grant already in your saved list (updated).")
    return redirect(request.POST.get("next") or "auth:home")


@login_required
@require_POST
def unsave_grant_view(request, saved_id: int):
    grant = get_object_or_404(SavedGrant, pk=saved_id, user=request.user)
    grant.delete()
    messages.success(request, "Removed from saved grants.")
    return redirect(request.POST.get("next") or "accounts:saved_grants")


def _digest_trigger_authorized(request) -> bool:
    secret = settings.N8N_WEBHOOK_AUTH_HEADER_VALUE
    if not secret:
        return False
    provided = request.headers.get(settings.N8N_WEBHOOK_AUTH_HEADER, "")
    if not provided:
        bearer = request.headers.get("Authorization", "")
        provided = bearer[7:] if bearer.lower().startswith("bearer ") else bearer
    return constant_time_compare(provided.strip(), secret)


def _flag(request, name: str) -> bool:
    return (request.GET.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _strip_payloads(report: dict) -> dict:
    """Digest payloads embed the full email HTML — too big for a trigger response."""
    trimmed = dict(report)
    trimmed["results"] = [
        {key: value for key, value in result.items() if key != "payload"}
        for result in report.get("results", [])
    ]
    return trimmed


@csrf_exempt
@require_POST
def run_weekly_digests_view(request):
    """
    Machine endpoint so an external scheduler (n8n Schedule Trigger, cron, Task
    Scheduler) can kick off the weekly digest run.

    Auth: send the same shared secret used for the outbound webhook, either as
    the configured header (default `X-Webhook-Token`) or `Authorization: Bearer`.

    Query flags: `force=1` (ignore the Monday/already-sent guards),
    `dry_run=1` (build only), `wait=1` (block and return the full report),
    `limit=N`, `username=a&username=b`.
    """
    if not _digest_trigger_authorized(request):
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=401)

    force = _flag(request, "force")
    dry_run = _flag(request, "dry_run")
    usernames = [name for name in request.GET.getlist("username") if name.strip()]
    try:
        limit = int(request.GET.get("limit")) if request.GET.get("limit") else None
    except ValueError:
        return JsonResponse({"ok": False, "error": "invalid_limit"}, status=400)

    kwargs = {
        "usernames": usernames,
        "force": force,
        "dry_run": dry_run,
        "limit": limit,
        "require_digest_day": not force,
    }

    if not force and not is_digest_day():
        return JsonResponse(
            {
                "ok": True,
                "started": False,
                "week_start": week_start_for().isoformat(),
                "detail": "Weekly digests are sent on Mondays (UTC). Pass force=1 to override.",
            }
        )

    # Matching every user runs the LLM pipeline, so the default is fire-and-forget
    # to keep the caller (n8n HTTP Request) from timing out.
    if not _flag(request, "wait"):
        def _run() -> None:
            try:
                send_weekly_digests(**kwargs)
            except Exception:
                logger.exception("Background weekly digest run failed")
            finally:
                connection.close()

        threading.Thread(target=_run, name="weekly-digest-run", daemon=True).start()
        return JsonResponse(
            {
                "ok": True,
                "started": True,
                "mode": "background",
                "week_start": week_start_for().isoformat(),
                "detail": "Digest run started; each user's payload is POSTed to the n8n webhook.",
            },
            status=202,
        )

    try:
        report = send_weekly_digests(**kwargs)
    except DigestConfigError as exc:
        return JsonResponse(
            {"ok": False, "error": "webhook_not_configured", "detail": str(exc)},
            status=503,
        )
    except Exception:
        logger.exception("Weekly digest run failed")
        return JsonResponse({"ok": False, "error": "digest_failed"}, status=500)

    return JsonResponse({"ok": True, "started": True, **_strip_payloads(report)})
