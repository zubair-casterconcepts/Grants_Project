from collections import Counter

from django.contrib.auth import get_user_model

from .models import GrantFeedback, Profile

User = get_user_model()

# How many times a funder/category must be rejected before it counts as a
# pattern rather than a one-off "not this particular grant".
NEGATIVE_PATTERN_THRESHOLD = 2


def get_or_create_profile(user: User) -> Profile:
    profile, _ = Profile.objects.get_or_create(user=user)
    return profile


def feedback_signals(user) -> dict:
    """
    Condense a user's past verdicts into ranking signals for the matcher.

    - `suppressed_keys`: exact opportunities they already rejected — never show
      these again.
    - negative/positive agencies + categories: patterns strong enough to nudge
      the score of *similar* opportunities next time.
    """
    rows = list(
        GrantFeedback.objects.filter(user=user).only(
            "source", "external_id", "verdict", "agency", "category"
        )
    )
    if not rows:
        return {}

    suppressed: set[str] = set()
    negative_agencies: Counter = Counter()
    positive_agencies: Counter = Counter()
    negative_categories: Counter = Counter()
    positive_categories: Counter = Counter()

    for row in rows:
        agency = (row.agency or "").strip().lower()
        category = (row.category or "").strip().lower()
        if row.is_negative:
            suppressed.add(f"{row.source}:{row.external_id}")
            if agency:
                negative_agencies[agency] += 1
            if category:
                negative_categories[category] += 1
        else:
            if agency:
                positive_agencies[agency] += 1
            if category:
                positive_categories[category] += 1

    def _pattern(counter: Counter) -> dict:
        return {
            key: count
            for key, count in counter.items()
            if count >= NEGATIVE_PATTERN_THRESHOLD
        }

    return {
        "suppressed_keys": suppressed,
        # A single "good match" is enough to boost; rejection needs a pattern so
        # one bad grant from a big funder doesn't blacklist the whole agency.
        "negative_agencies": _pattern(negative_agencies),
        "negative_categories": _pattern(negative_categories),
        "positive_agencies": dict(positive_agencies),
        "positive_categories": dict(positive_categories),
    }
