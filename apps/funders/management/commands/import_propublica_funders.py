"""
Save every organization from the ProPublica Nonprofit Explorer search API into Funder.

No foundation filter here: everything is saved so it can be filtered later in the
database (e.g. ntee_code starting with "T" = grantmaking foundations).
"""

import json
import time

import httpx
from django.core.management.base import BaseCommand, CommandError

from apps.funders.models import Funder

SEARCH_URL = "https://projects.propublica.org/nonprofits/api/v2/search.json"


class Command(BaseCommand):
    help = "Import all organizations from ProPublica search (default: q=foundation, state=MI)."

    def add_arguments(self, parser):
        parser.add_argument("--q", default="foundation")
        parser.add_argument("--state", default="MI")

    def get_page(self, client, params, page):
        for attempt in range(1, 4):
            try:
                response = client.get(SEARCH_URL, params={**params, "page": page})
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                self.stderr.write(f"page {page} failed (attempt {attempt}/3): {exc}")
                time.sleep(5 * attempt)
        raise CommandError(f"Stopped: page {page} could not be fetched. Run again to continue.")

    def handle(self, *args, **options):
        params = {"q": options["q"], "state[id]": options["state"]}
        saved = skipped = 0
        page = 0
        num_pages = 1
        with httpx.Client(timeout=30) as client:
            while page < num_pages:
                data = self.get_page(client, params, page)
                num_pages = data.get("num_pages") or 0
                organizations = data.get("organizations") or []
                if page == 0:
                    self.stdout.write(f"total_results={data.get('total_results')} num_pages={num_pages}")
                    if organizations:
                        self.stdout.write("First result:\n" + json.dumps(organizations[0], indent=2))

                funders = {}
                for org in organizations:
                    if not org.get("ein"):
                        skipped += 1
                        continue
                    ein = str(org["ein"]).zfill(9)
                    funders[ein] = Funder(
                        ein=ein,
                        name=(org.get("name") or "")[:255],
                        sub_name=(org.get("sub_name") or "")[:255],
                        city=(org.get("city") or "")[:100],
                        state=(org.get("state") or "")[:2],
                        ntee_code=(org.get("ntee_code") or "")[:10],
                        subsection_code=org.get("subseccd"),
                        raw_data=org,
                    )
                # One insert-or-update query per page instead of two per row.
                Funder.objects.bulk_create(
                    funders.values(),
                    update_conflicts=True,
                    unique_fields=["ein"],
                    update_fields=["name", "sub_name", "city", "state", "ntee_code",
                                   "subsection_code", "raw_data", "updated_at"],
                )
                saved += len(funders)

                self.stdout.write(f"page {page + 1}/{num_pages}: saved {saved}, skipped {skipped}")
                page += 1
                if page < num_pages:
                    time.sleep(1)  # be polite

        self.stdout.write(self.style.SUCCESS(
            f"Done. Saved/updated {saved} organizations, skipped {skipped} without an EIN. "
            f"Funder rows: {Funder.objects.count()}"
        ))
