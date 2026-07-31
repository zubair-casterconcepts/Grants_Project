from django.core.management.base import BaseCommand

from services.granted_ai import search_grants


class Command(BaseCommand):
    help = "Smoke-test the GrantedAI search client."

    def add_arguments(self, parser):
        parser.add_argument("--keyword", default="community health")
        parser.add_argument("--state", default="NY")
        parser.add_argument("--priority", default="Health")
        parser.add_argument("--limit", type=int, default=5)

    def handle(self, *args, **options):
        rows = search_grants(
            keyword=options["keyword"],
            priority_area=options["priority"],
            location_state=options["state"],
            limit=options["limit"],
        )
        self.stdout.write(self.style.SUCCESS(f"GrantedAI returned {len(rows)} row(s)"))
        for row in rows[:5]:
            self.stdout.write(
                f"- {row.get('title', '')[:80]} | {row.get('agency', '')} | "
                f"{row.get('amount', '')} | {row.get('deadline', '')}"
            )
