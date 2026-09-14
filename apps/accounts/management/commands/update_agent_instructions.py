"""
Check the past week's feedback reasons against the agent's system prompt and
patch it where they conflict.

Celery beat runs this at the end of every week (CELERY_BEAT_SCHEDULE). Without
Celery, schedule this command with cron or Windows Task Scheduler instead:

    python manage.py update_agent_instructions

Preview conflicts and the exact prompt changes without saving anything:

    python manage.py update_agent_instructions --dry-run --force

Print the full system prompt the agent currently uses:

    python manage.py update_agent_instructions --show

Save grant_agent_instructions.md as a new active prompt version (after editing
the file by hand):

    python manage.py update_agent_instructions --import-file
"""

from django.core.management.base import BaseCommand, CommandError

from services.instruction_learning import (
    LOOKBACK_DAYS,
    active_prompt_record,
    clear_guidance_cache,
    file_instructions,
    normalize_prompt,
    save_prompt_version,
    update_agent_instructions,
)


class Command(BaseCommand):
    help = "Patch the agent's system prompt where the past week's feedback conflicts with it."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=LOOKBACK_DAYS,
            help=f"How many days of feedback to check (default {LOOKBACK_DAYS}).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show conflicts and the proposed prompt changes without saving anything.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even if the prompt was already updated this week.",
        )
        parser.add_argument(
            "--show",
            action="store_true",
            help="Print the full system prompt the agent currently uses, then exit.",
        )
        parser.add_argument(
            "--import-file",
            action="store_true",
            help="Save grant_agent_instructions.md as a new active prompt version, then exit.",
        )

    def handle(self, *args, **options):
        if options["show"]:
            from services.grant_agent import load_agent_instructions

            clear_guidance_cache()
            self.stdout.write(load_agent_instructions())
            return

        if options["import_file"]:
            from apps.accounts.models import AgentSystemPrompt

            text = file_instructions()
            if not text:
                raise CommandError("services/grant_agent_instructions.md is missing or empty.")
            current = active_prompt_record()
            if current and normalize_prompt(current.content) == text:
                self.stdout.write("The active prompt already matches the file; nothing imported.")
                return
            prompt = save_prompt_version(
                text,
                source=AgentSystemPrompt.Source.FILE.value,
                based_on=current,
                change_summary="- Imported grant_agent_instructions.md.",
            )
            self.stdout.write(
                self.style.SUCCESS(f"Imported the file as version {prompt.version} and activated it.")
            )
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
        conflicts = report.get("conflicts") or []
        if conflicts:
            self.stdout.write("  Conflicts:")
            for conflict in conflicts:
                self.stdout.write(f"   - {conflict['explanation']}")
        summary = report.get("summary") or []
        if summary:
            self.stdout.write("  Changes:")
            for line in summary:
                self.stdout.write(f"   - {line}")
        if options["dry_run"] and report.get("diff"):
            self.stdout.write("  Proposed prompt diff:")
            self.stdout.write(report["diff"])
        if report.get("update_id"):
            self.stdout.write(
                self.style.SUCCESS(
                    f"  Saved as prompt version {report['prompt_version']} "
                    f"(run #{report['update_id']}) and activated."
                )
            )
