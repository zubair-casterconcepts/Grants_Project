from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import AbstractBaseUser


def attempt_login(request, username: str, password: str) -> AbstractBaseUser | None:
    """Authenticate credentials and create a session. Returns the user or None."""
    user = authenticate(
        request,
        username=username.strip(),
        password=password,
    )
    if user is None or not user.is_active:
        return None
    login(request, user)
    return user


def attempt_logout(request) -> None:
    logout(request)
