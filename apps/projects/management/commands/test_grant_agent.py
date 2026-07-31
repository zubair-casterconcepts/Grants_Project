import json

from django.core.management.base import BaseCommand

from apps.accounts.models import Profile
from services.grant_agent import run_grant_matching_agent


class Command(BaseCommand):
    help = "Run the OpenAI grant-matching agent (with Grants.gov tool) and print JSON."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default="admin",
            help="grant_user username whose profile to match (default: admin)",
        )

    def handle(self, *args, **options):
        username = options["username"]
        profile = (
            Profile.objects.select_related("user")
            .filter(user__username=username)
            .first()
        )
        if profile is None:
            self.stderr.write(self.style.ERROR(f"No profile for username={username!r}"))
            return

        self.stdout.write(
            "Running grant agent for "
            f"{username!r} title={profile.title!r} "
            f"priority={profile.priority_area!r} "
            f"location={profile.location_city!r}, {profile.location_state!r}"
        )
        matches = run_grant_matching_agent(profile)
        self.stdout.write(json.dumps(matches, indent=2))
        self.stdout.write(self.style.SUCCESS(f"\n{len(matches)} match(es)"))
