"""Celery tasks for the accounts app."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from celery import shared_task
except ImportError:  # Celery is optional until worker/beat are running.
    def shared_task(*_args, **_kwargs):
        def _decorator(fn):
            return fn

        return _decorator


@shared_task(name="accounts.send_weekly_digests")
def send_weekly_digests_task(force: bool = False) -> dict:
    """
    Monday job: build digests from each user's profile and POST them to the
    n8n webhook (Outlook sends the email).

    Pass force=True for test resends (ignores Monday/already-sent guards).
    """
    from services.weekly_digest import DigestConfigError, send_weekly_digests

    try:
        report = send_weekly_digests(
            force=force,
            require_digest_day=not force,
        )
    except DigestConfigError:
        logger.exception("Weekly digest Celery task skipped — webhook not configured")
        raise

    summary = {
        key: report.get(key)
        for key in (
            "week_start",
            "considered",
            "sent",
            "skipped",
            "failed",
            "skipped_reason",
        )
        if key in report
    }
    logger.info("Weekly digest Celery task finished %s", summary)
    return summary
