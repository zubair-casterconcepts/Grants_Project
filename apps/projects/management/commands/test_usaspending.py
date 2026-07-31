import json
from argparse import ArgumentParser

from django.core.management.base import BaseCommand

from services.usaspending import search_awards


class Command(BaseCommand):
    help = "Call USASpending spending_by_award with sample/location filters; print JSON."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--keyword", default="community health")
        parser.add_argument("--priority-area", default="Health")
        parser.add_argument("--city", default="New York")
        parser.add_argument("--state", default="NY")

    def handle(self, *args, **options):
        self.stdout.write(
            "Searching USASpending "
            f"keyword={options['keyword']!r} "
            f"priority={options['priority_area']!r} "
            f"location={options['city']!r}, {options['state']!r}"
        )
        results = search_awards(
            keyword=options["keyword"],
            priority_area=options["priority_area"],
            location_city=options["city"],
            location_state=options["state"],
            limit=10,
        )
        self.stdout.write(json.dumps(results, indent=2))
        self.stdout.write(self.style.SUCCESS(f"\n{len(results)} result(s)"))
