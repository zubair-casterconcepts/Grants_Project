"""
Build each user's weekly grant digest and POST it to the n8n webhook.

Schedule this every Monday (or daily — the send is keyed to the Monday of the
current week, so extra runs are no-ops):

    python manage.py send_weekly_digests

Preview without calling n8n:

    python manage.py send_weekly_digests --username admin --dry-run --force \
        --print-payload --save-html preview.html
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from services.weekly_digest import (
    DigestConfigError,
    is_digest_day,
    send_weekly_digests,
    webhook_configured,
)


class Command(BaseCommand):
    help = "Send the weekly grant digest for every opted-in user via the n8n webhook."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            action="append",
            dest="usernames",
            default=[],
            help="Limit to this username (repeatable). Default: all eligible users.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Send even if it is not Monday and even if this week was already sent.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Build payloads but do not call the webhook or write the digest log.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Process at most N users (useful for a first smoke test).",
        )
        parser.add_argument(
            "--max-grants",
            type=int,
            default=None,
            help=(
                "Grants per email. Default comes from WEEKLY_DIGEST_MAX_GRANTS "
                "(0 = every ranked match)."
            ),
        )
        parser.add_argument(
            "--print-payload",
            action="store_true",
            help="Print the full JSON payload sent to n8n (email.html omitted).",
        )
        parser.add_argument(
            "--save-html",
            default="",
            help="Write the first user's rendered email HTML to this path.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force = options["force"]

        if not dry_run and not webhook_configured():
            raise CommandError(
                "N8N_WEEKLY_GRANTED_WEBHOOK_URL is not set in .env — nothing to send to."
            )
        if not force and not is_digest_day():
            self.stdout.write(
                "Today is not Monday (UTC); nothing to do. Use --force to send anyway."
            )
            return

        try:
            report = send_weekly_digests(
                usernames=options["usernames"],
                force=force,
                dry_run=dry_run,
                limit=options["limit"],
                max_grants=options["max_grants"],
                require_digest_day=not force,
            )
        except DigestConfigError as exc:
            raise CommandError(str(exc)) from exc

        if report.get("skipped_reason"):
            self.stdout.write(report.get("detail") or "Skipped.")
            return

        mode = " (dry run)" if dry_run else ""
        self.stdout.write(
            f"Week of {report['week_start']}{mode}: "
            f"{report['considered']} considered, {report['sent']} sent, "
            f"{report['skipped']} skipped, {report['failed']} failed."
        )
        for result in report["results"]:
            line = (
                f"  {result['status']:>7}  {result['username']:<18} "
                f"{result.get('match_count', 0)} match(es)  {result.get('detail', '')}"
            )
            if result["status"] == "failed":
                self.stderr.write(self.style.ERROR(line))
            else:
                self.stdout.write(line)

        payloads = [r["payload"] for r in report["results"] if r.get("payload")]

        html_path = (options["save_html"] or "").strip()
        if html_path and payloads:
            Path(html_path).write_text(payloads[0]["email"]["html"], encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Email preview written to {html_path}"))

        if options["print_payload"]:
            for payload in payloads:
                shown = dict(payload)
                shown["email"] = {
                    key: value
                    for key, value in payload["email"].items()
                    if key not in {"html", "text"}
                }
                shown["email"]["html"] = f"<{len(payload['email']['html'])} chars omitted>"
                shown["email"]["text"] = f"<{len(payload['email']['text'])} chars omitted>"
                self.stdout.write(json.dumps(shown, indent=2, default=str))

        if report["failed"]:
            raise CommandError(f"{report['failed']} digest(s) failed.")
