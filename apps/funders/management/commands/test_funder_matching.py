"""Print match_funders results for a few sample projects (read-only)."""

from django.core.management.base import BaseCommand

from apps.funders.matching import match_funders
from apps.funders.models import FunderContact, FunderProfile

CASES = [
    ("Youth Development", 10_000),
    ("Food Access/Food Rescue", 25_000),
    ("Arts", 5_000),
]


class Command(BaseCommand):
    help = "Run match_funders on sample cases and print the results."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=10)

    def handle(self, *args, **opts):
        w = self.stdout.write
        w(f"FunderProfiles: {FunderProfile.objects.count()} | funders with a contact that accepts requests: "
          f"{FunderContact.objects.filter(accepts_unsolicited=True).values('funder').distinct().count()}")
        for area, budget in CASES:
            results = match_funders(area, budget=budget, state="MI", limit=opts["limit"])
            w("")
            w("=" * 110)
            w(f"match_funders(priority_area={area!r}, budget={budget:,}, state='MI') -> top {len(results)}")
            w("=" * 110)
            for i, r in enumerate(results, 1):
                accepts = {True: "YES", False: "NO (pre-selected only)", None: "no contact listed"}[r["accepts_requests"]]
                w(f"{i:2}. {r['funder']}  score {r['score']}  {r['score_parts']}")
                w(f"    {r['grants_in_category']} grants / ${r['amount_in_category']:,.0f} in {area}; "
                  f"median ${r['typical_grant_in_category']:,}; MI {r['michigan_grants_pct']}%; "
                  f"top {r['top_categories']}")
                w(f"    accepts requests: {accepts}"
                  + (f" | {r['contact']['name']} {r['contact']['phone']} | {r['contact']['address'][:60]}"
                     if r["contact"] else ""))
                for g in r["example_grants"]:
                    w(f"      e.g. {g['tax_year']} ${g['amount']:,.0f} -> {g['recipient_name'][:40]} "
                      f"({g['recipient_city']} {g['recipient_state']}): {g['purpose'][:50]}")
