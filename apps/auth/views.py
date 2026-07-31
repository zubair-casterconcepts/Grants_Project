from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods

from apps.accounts.models import SavedGrant
from apps.accounts.services import get_or_create_profile
from services.grant_agent import run_grant_matching_agent

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


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("auth:home")

    if request.method == "POST":
        username = request.POST.get("username", "")
        password = request.POST.get("password", "")
        user = attempt_login(request, username, password)
        if user is not None:
            profile = get_or_create_profile(user)
            next_url = request.POST.get("next") or request.GET.get("next")
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            if profile.needs_onboarding:
                return redirect("accounts:onboarding")
            return redirect("auth:home")
        messages.error(request, "Invalid username or password.")

    return render(
        request,
        "login.html",
        {"next": request.GET.get("next", "")},
    )


@require_http_methods(["POST"])
def logout_view(request):
    attempt_logout(request)
    messages.success(request, "You have been signed out.")
    return redirect("auth:login")


@login_required
def home_view(request):
    """Render dashboard shell quickly; matches load asynchronously."""
    profile = get_or_create_profile(request.user)
    if profile.needs_onboarding:
        return redirect("accounts:onboarding")

    saved_count = SavedGrant.objects.filter(user=request.user).count()
    return render(
        request,
        "home.html",
        {
            "profile": profile,
            "matches": [],
            "match_count": 0,
            "hidden_match_count": 0,
            "saved_count": saved_count,
            "matches_api_url": "/home/matches/",
        },
    )


@login_required
@require_GET
def matches_api_view(request):
    """JSON endpoint used by the dashboard grants loader."""
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
