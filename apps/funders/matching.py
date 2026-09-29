"""Match a project (priority area + budget) to funders who have actually given to that area."""

import logging
import math
import re

from apps.funders.models import FunderContact, FunderProfile, PastGrant

logger = logging.getLogger(__name__)

# App priority areas (services/grant_categories.py) that are named differently in
# the grant labels. App areas with no grant label at all (Environment, Veterans,
# Agriculture, ...) have no foundation data: those grants were labeled "Other".
APP_AREA_TO_LABEL = {"Food Access": "Food Access/Food Rescue"}
SEARCH_LIMIT = 6  # foundations shown under a chat search

W_SHARE, W_SIZE = 0.65, 0.35   # fit to the category / to the budget (size dropped when no budget)
FULL_CONFIDENCE_GRANTS = 10     # a share is fully trusted from this many grants in the category
STATE_FLOOR = 0.25              # MI search: a funder with 0% Michigan giving keeps only 25% of its score
CONTACT_BOOST = 0.10            # lift for funders whose latest filing accepts requests


def size_fit(typical, budget):
    """1.0 when the funder's typical grant equals the budget, 0.5 at 10x off, 0 at 100x off."""
    if not budget or typical <= 0:
        return 0.0
    return max(0.0, 1 - abs(math.log10(typical / budget)) / 2)


def latest_contact_flags():
    """{funder id: accepts_unsolicited of its most recent application contact}."""
    flags = {}
    rows = (FunderContact.objects.order_by("funder_id", "-filing__tax_period", "-id")
            .values_list("funder_id", "accepts_unsolicited"))
    for funder_id, accepts in rows:
        flags.setdefault(funder_id, accepts)
    return flags


def contact_for(funder):
    """The funder's most recent application contact (or None)."""
    contact = (FunderContact.objects.filter(funder=funder)
               .select_related("filing").order_by("-filing__tax_period", "-id").first())
    if contact is None:
        return None
    return {"name": contact.contact_name, "phone": contact.phone,
            "address": contact.address, "how_to_apply": contact.notes,
            "tax_period": contact.filing.tax_period if contact.filing else ""}


def example_grants(funder, priority_area, n=3):
    rows, seen = [], set()
    qs = (PastGrant.objects.filter(funder=funder, priority_area=priority_area, amount__gt=0)
          .order_by("-tax_year", "-amount").values("recipient_name", "recipient_city", "recipient_state",
                                                   "amount", "purpose", "tax_year"))
    for g in qs[:20]:
        if g["recipient_name"] in seen:
            continue
        seen.add(g["recipient_name"])
        rows.append({**g, "amount": float(g["amount"])})
        if len(rows) == n:
            break
    return rows


def match_funders(priority_area, budget=None, state=None, limit=20):
    """
    Funders ranked for a project in `priority_area`.

    score = confidence x (0.65 * share + 0.35 * size fit) / weights used
      share      - part of the funder's giving that went to this area
      confidence - min(grants in this area, 10) / 10, so one lucky grant can't win
      size fit   - how close its median grant in this area is to `budget` (if given)
    For a Michigan search (state="MI") the score is then scaled by Michigan
    giving: x (0.25 + 0.75 * share of grants to MI). Without a state it is neutral.
    Funders whose latest filing accepts requests get +0.10. Nobody is filtered out.
    """
    in_mi = (state or "").upper() == "MI"
    accepts_by_funder = latest_contact_flags()

    scored = []
    for p in FunderProfile.objects.filter(category_breakdown__has_key=priority_area).select_related("funder"):
        cat = p.category_breakdown[priority_area]
        amount, count = cat["total_amount"], cat["count"]
        if amount <= 0 or count <= 0 or p.total_amount <= 0:
            continue
        typical = cat.get("median_amount") or amount / count
        share = min(1.0, amount / float(p.total_amount))
        confidence = min(count, FULL_CONFIDENCE_GRANTS) / FULL_CONFIDENCE_GRANTS
        fit = size_fit(typical, budget)
        # Confidence scales both parts: one grant says little about either the
        # funder's focus or its typical grant size.
        base = confidence * (W_SHARE * share + (W_SIZE * fit if budget else 0)) / (W_SHARE + (W_SIZE if budget else 0))
        state_factor = STATE_FLOOR + (1 - STATE_FLOOR) * p.michigan_grants_pct / 100 if in_mi else 1.0
        accepts = accepts_by_funder.get(p.funder_id)
        score = base * state_factor + (CONTACT_BOOST if accepts else 0)
        parts = {"share": share, "confidence": confidence, "size": fit, "state_factor": state_factor,
                 "contact_boost": CONTACT_BOOST if accepts else 0}
        scored.append((score, p, amount, count, typical, parts, accepts))

    scored.sort(key=lambda x: -x[0])
    results = []
    for score, p, amount, count, typical, parts, accepts in scored[:limit]:
        results.append({
            "funder": p.funder.name,
            "ein": p.funder.ein,
            "score": round(score, 3),
            "score_parts": {k: round(v, 2) for k, v in parts.items()},
            "top_categories": p.top_categories,
            "grants_in_category": count,
            "amount_in_category": amount,
            "typical_grant_in_category": round(typical),  # median
            "michigan_grants_pct": p.michigan_grants_pct,
            "accepts_requests": accepts,  # True / False (pre-selected only) / None (no contact listed)
            "contact": contact_for(p.funder) if accepts is not None else None,
            "example_grants": example_grants(p.funder, priority_area),
        })
    return results


def parse_budget(value):
    """"50000", "$50,000" or a range like "5000-100000" (midpoint) -> float, or None."""
    numbers = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", str(value or ""))]
    numbers = [n for n in numbers if n > 0]
    if not numbers:
        return None
    return sum(numbers[:2]) / len(numbers[:2])


def funders_for_search(context, limit=SEARCH_LIMIT):
    """
    Foundations for a chat grants search, from the fields that search already
    resolved (priority_area, budget_requested, location_state). Never raises:
    a failure returns an empty list so the grants results are unaffected.
    """
    app_area = str(context.get("priority_area") or "").strip()
    area = APP_AREA_TO_LABEL.get(app_area, app_area)
    state = str(context.get("location_state") or "").strip().upper()
    budget = parse_budget(context.get("budget_requested"))
    out = {"priority_area": area, "state": state, "budget": budget, "funders": []}
    if not area:
        return out
    try:
        out["funders"] = match_funders(area, budget=budget, state=state or None, limit=limit)
    except Exception:
        logger.warning("funder matching failed for %r", area, exc_info=True)
    return out
