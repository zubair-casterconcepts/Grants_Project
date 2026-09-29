"""Build one FunderProfile per funder from its classified PastGrants (reads PastGrant, never changes it)."""

from collections import defaultdict
from decimal import Decimal
from statistics import median

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.funders.models import Filing, FunderProfile, PastGrant

NOT_A_FOCUS = {"Unclear", "Other"}  # counted in totals, never a top category


def latest_filing_ids():
    """
    One filing per funder + tax period. When a return was amended there are two
    filings for the same year; using only the latest (highest object_id) keeps
    its grants from being counted twice.
    """
    latest = {}
    for fid, funder_id, period, object_id in Filing.objects.values_list("id", "funder_id", "tax_period", "object_id"):
        key = (funder_id, period)
        if key not in latest or object_id > latest[key][1]:
            latest[key] = (fid, object_id)
    return {fid for fid, _ in latest.values()}


class Command(BaseCommand):
    help = "Aggregate PastGrant by priority_area into FunderProfile."

    def handle(self, *args, **opts):
        use_filings = latest_filing_ids()
        stats = defaultdict(lambda: {
            "cats": defaultdict(lambda: {"count": 0, "total_amount": Decimal(0)}),
            "total": 0, "amount": Decimal(0), "mi": 0, "years": set(),
        })
        skipped_amended = 0
        grants = PastGrant.objects.values_list(
            "funder_id", "filing_id", "priority_area", "amount", "recipient_state", "tax_year")
        for funder_id, filing_id, area, amount, state, year in grants.iterator(chunk_size=10000):
            if filing_id not in use_filings:
                skipped_amended += 1
                continue
            s = stats[funder_id]
            amount = amount or Decimal(0)
            cat = s["cats"][area or "Unclear"]
            cat["count"] += 1
            cat["total_amount"] += amount
            if amount > 0:
                cat.setdefault("amounts", []).append(amount)
            s["total"] += 1
            s["amount"] += amount
            s["mi"] += state == "MI"
            if year:
                s["years"].add(year)

        profiles = []
        for funder_id, s in stats.items():
            focus = [(c, v["total_amount"]) for c, v in s["cats"].items()
                     if c not in NOT_A_FOCUS and v["total_amount"] > 0]
            profiles.append(FunderProfile(
                funder_id=funder_id,
                # median_amount = typical grant size in the category (one outlier grant can't skew it)
                category_breakdown={c: {"count": v["count"], "total_amount": float(v["total_amount"]),
                                        "median_amount": float(median(v["amounts"])) if v.get("amounts") else 0.0}
                                    for c, v in sorted(s["cats"].items(), key=lambda kv: -kv[1]["total_amount"])},
                top_categories=[c for c, _ in sorted(focus, key=lambda x: -x[1])[:3]],
                total_grants=s["total"],
                total_amount=s["amount"],
                avg_grant_amount=(s["amount"] / s["total"]).quantize(Decimal("0.01")),
                michigan_grants_count=s["mi"],
                michigan_grants_pct=round(100 * s["mi"] / s["total"], 1),
                years_with_data=sorted(s["years"]),
            ))

        with transaction.atomic():
            FunderProfile.objects.all().delete()  # rebuilt from scratch each run
            FunderProfile.objects.bulk_create(profiles, batch_size=1000)

        self.stdout.write(self.style.SUCCESS(
            f"Created {len(profiles)} funder profiles "
            f"({skipped_amended} grants from superseded amended filings not counted)"))
        self.stdout.write("Sample of 10:")
        for p in FunderProfile.objects.select_related("funder").order_by("?")[:10]:
            self.stdout.write(
                f"  {p.funder.name[:38]:38} | grants {p.total_grants:4} | ${p.total_amount:>14,.0f} | "
                f"avg ${p.avg_grant_amount:>11,.0f} | MI {p.michigan_grants_pct:5.1f}% | "
                f"years {p.years_with_data} | top {p.top_categories}")
