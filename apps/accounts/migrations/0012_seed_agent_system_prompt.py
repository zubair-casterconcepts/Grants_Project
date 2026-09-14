"""
Store the agent's current system prompt as version 1.

Version 1 is exactly what the agent used before this migration:
grant_agent_instructions.md, plus the learned-rules section from the active
weekly update if there was one. Nothing about the agent's behaviour changes.
"""

from pathlib import Path

from django.db import migrations
from django.utils import timezone

INSTRUCTIONS_FILE = Path(__file__).resolve().parents[3] / "services" / "grant_agent_instructions.md"


def _legacy_learned_section(update):
    rules = [
        line.strip()[2:].strip()
        for line in (update.guidance or "").splitlines()
        if line.strip().startswith("- ") and line.strip()[2:].strip()
    ]
    if not rules:
        return ""
    week_ending = timezone.localtime(update.period_end).date().isoformat()
    bullets = "\n".join(f"- {rule}" for rule in rules)
    return (
        f"## Learned from grant-writer feedback (week ending {week_ending})\n\n"
        "These rules were distilled automatically from grant writers' reasons for "
        "marking past recommendations Good match, Not eligible or Not relevant. Apply "
        "them when choosing and ranking opportunities. If one conflicts with an "
        "eligibility rule above, the rule above wins.\n\n"
        f"{bullets}"
    )


def seed(apps, schema_editor):
    AgentSystemPrompt = apps.get_model("accounts", "AgentSystemPrompt")
    AgentInstructionUpdate = apps.get_model("accounts", "AgentInstructionUpdate")
    if AgentSystemPrompt.objects.exists():
        return
    try:
        base = INSTRUCTIONS_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        base = ""
    if not base:
        # The loader falls back to the file; import it later with
        # `manage.py update_agent_instructions --import-file`.
        return

    content = base
    summary = "- Imported grant_agent_instructions.md."
    update = (
        AgentInstructionUpdate.objects.filter(is_active=True, status="applied")
        .order_by("-created_at")
        .first()
    )
    section = _legacy_learned_section(update) if update else ""
    if section:
        content = f"{base}\n\n{section}"
        summary += "\n- Included the learned rules that were active at the time."

    content = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    prompt = AgentSystemPrompt.objects.create(
        version=1,
        content=content,
        source="file",
        change_summary=summary,
        is_active=True,
    )
    if section:
        AgentInstructionUpdate.objects.filter(pk=update.pk).update(prompt=prompt)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0011_agent_system_prompt"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
