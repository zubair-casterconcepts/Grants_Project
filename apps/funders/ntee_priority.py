"""Free priority-area tag for a grant from its recipient's own IRS NTEE code (no AI, no guessing)."""

NTEE_MAJOR_TO_PRIORITY = {
    "A": None,              # Arts/Culture — needs sub-code, leave for AI step
    "B": "Education",
    "E": "Health", "F": "Health", "G": "Health", "H": "Health",
    "I": "Public Safety",
    "J": "Workforce Development",
    "K": "Food Access/Food Rescue",
    "L": "Housing",
    "N": "Recreation",
    "O": "Youth Development",
    "P": "Human Services",
    "S": "Community Development",   # also covers some Economic/Downtown Dev — needs review
}
# Every other letter (C, D, M, Q, R, T, U, V, W, X, Y, Z) is unmapped for now.

SKIP_RECIPIENTS = {"", "(individual)"}


def name_key(name):
    """Exact match key: trimmed and case-insensitive only (no fuzzy matching)."""
    return (name or "").strip().casefold()


def ntee_letter(ntee_code):
    return (ntee_code or "").strip()[:1].upper()


def priority_for(ntee_code):
    """Priority area for an NTEE code, or None if its major letter is unmapped / None."""
    return NTEE_MAJOR_TO_PRIORITY.get(ntee_letter(ntee_code))


def org_index():
    """Saved Michigan orgs by exact-match name key -> [(name, ein, ntee_code), ...]."""
    from collections import defaultdict

    from apps.funders.models import Funder

    orgs = defaultdict(list)
    for name, ein, ntee in Funder.objects.values_list("name", "ein", "ntee_code"):
        orgs[name_key(name)].append((name, ein, ntee))
    return orgs


def ntee_tags():
    """{PastGrant id: priority area} for grants whose recipient exactly matches one org with a mapped NTEE letter."""
    from apps.funders.models import PastGrant

    orgs = org_index()
    tags = {}
    for gid, recipient in PastGrant.objects.values_list("id", "recipient_name").iterator(chunk_size=5000):
        key = name_key(recipient)
        candidates = orgs.get(key) if key not in SKIP_RECIPIENTS else None
        if candidates and len(candidates) == 1:
            priority = priority_for(candidates[0][2])
            if priority:
                tags[gid] = priority
    return tags
