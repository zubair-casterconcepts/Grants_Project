"""
Learn agent instruction rules from the past week's feedback reasons.

Celery beat runs this at the end of every week (CELERY_BEAT_SCHEDULE). Without
Celery, schedule this command with cron or Windows Task Scheduler instead:

    python manage.py update_agent_instructions

Preview the rules without saving or activating anything:

    python manage.py update_agent_instructions --dry-run --force

Print the full instructions the agent currently receives:

    python manage.py update_agent_instructions --show
"""

from django.core.management.base import BaseCommand, CommandError

from services.instruction_learning import (
    LOOKBACK_DAYS,
    clear_guidance_cache,
    update_agent_instructions,
)


class Command(BaseCommand):
    help = "Distil the past week's feedback reasons into agent instruction rules."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=LOOKBACK_DAYS,
            help=f"How many days of feedback to learn from (default {LOOKBACK_DAYS}).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Generate and print the rules without saving or activating them.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even if the instructions were already updated this week.",
        )
        parser.add_argument(
            "--show",
            action="store_true",
            help="Print the full instructions the agent currently uses, then exit.",
        )

    def handle(self, *args, **options):
        if options["show"]:
            from services.grant_agent import load_agent_instructions

            clear_guidance_cache()
            self.stdout.write(load_agent_instructions())
            return

        days = options["days"]
        if days < 1:
            raise CommandError("--days must be at least 1.")

        report = update_agent_instructions(
            days=days,
            dry_run=options["dry_run"],
            force=options["force"],
        )

        mode = " (dry run)" if options["dry_run"] else ""
        self.stdout.write(
            f"{report['status'].upper()}{mode}: {report.get('feedback_count', 0)} "
            f"feedback reason(s) from the last {days} day(s) {report.get('verdicts') or ''}"
        )
        if report.get("detail"):
            self.stdout.write(f"  {report['detail']}")
        rules = report.get("rules") or []
        if rules:
            self.stdout.write(f"  Rules ({report.get('method')}):")
            for rule in rules:
                self.stdout.write(f"   - {rule}")
        if report.get("update_id"):
            self.stdout.write(
                self.style.SUCCESS(f"  Saved as update #{report['update_id']} and activated.")
            )
