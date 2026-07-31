from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from .forms import ProfileIntakeForm
from .models import SavedGrant
from .services import get_or_create_profile


@login_required
@require_http_methods(["GET", "POST"])
def onboarding_view(request):
    profile = get_or_create_profile(request.user)
    if profile.onboarding_completed:
        return redirect("auth:home")

    if request.method == "POST":
        form = ProfileIntakeForm(request.POST, instance=profile)
        if form.is_valid():
            profile = form.save(commit=False)
            profile.ntee_code = profile.ntee_code or ""
            profile.onboarding_completed = True
            profile.save()
            messages.success(request, "Your profile is ready. Welcome to Grants.")
            return redirect("auth:home")
    else:
        form = ProfileIntakeForm(instance=profile)

    return render(
        request,
        "accounts/onboarding.html",
        {"form": form, "profile": profile},
    )


@login_required
@require_http_methods(["GET", "POST"])
def settings_view(request):
    profile = get_or_create_profile(request.user)
    if profile.needs_onboarding:
        return redirect("accounts:onboarding")

    if request.method == "POST":
        form = ProfileIntakeForm(request.POST, instance=profile)
        if form.is_valid():
            profile = form.save(commit=False)
            profile.onboarding_completed = True
            profile.save()
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
def profile_view(request):
    return redirect("accounts:settings")


@login_required
@require_http_methods(["GET"])
def saved_grants_view(request):
    profile = get_or_create_profile(request.user)
    if profile.needs_onboarding:
        return redirect("accounts:onboarding")

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
        return redirect("accounts:onboarding")

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
