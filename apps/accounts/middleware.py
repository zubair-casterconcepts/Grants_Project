from django.shortcuts import redirect

from .services import get_or_create_profile

# Paths allowed before onboarding is finished.
_ALLOWED_PREFIXES = (
    "/accounts/onboarding",
    "/logout",
    "/admin",
    "/static",
)


class OnboardingMiddleware:
    """Send first-time users to complete their profile before the dashboard."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            path = request.path
            if not path.startswith(_ALLOWED_PREFIXES):
                # Login page itself is fine if somehow hit while authed.
                if path in ("/", "/login/", "/login"):
                    return self.get_response(request)
                profile = get_or_create_profile(user)
                if profile.needs_onboarding and not path.startswith("/accounts/onboarding"):
                    return redirect("accounts:onboarding")
        return self.get_response(request)
