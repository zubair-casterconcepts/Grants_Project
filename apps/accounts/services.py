from django.contrib.auth import get_user_model

from .models import Profile

User = get_user_model()


def get_or_create_profile(user: User) -> Profile:
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile
