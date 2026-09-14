from django.contrib.auth.models import AbstractUser
from django.db import models


class GrantUser(AbstractUser):
    """
    Custom user stored in Supabase Postgres as grant_user.
    Login uses username + password via Django auth sessions.
    """

    class Meta:
        db_table = "grant_user"
        verbose_name = "Grant user"
        verbose_name_plural = "Grant users"

    def __str__(self) -> str:
        return self.get_username()


class Profile(models.Model):
    """User intake + org details stored against grant_user."""

    class PriorityArea(models.TextChoices):
        ARTS = "Arts", "Arts"
        COMMUNITY_DEVELOPMENT = "Community Development", "Community Development"
        CULTURE = "Culture", "Culture"
        DOWNTOWN_DEVELOPMENT = "Downtown Development", "Downtown Development"
        ECONOMIC_DEVELOPMENT = "Economic Development", "Economic Development"
        EDUCATION = "Education", "Education"
        FOOD_ACCESS = "Food Access", "Food Access"
        HEALTH = "Health", "Health"
        HOUSING = "Housing", "Housing"
        HUMAN_SERVICES = "Human Services", "Human Services"
        LITERACY = "Literacy", "Literacy"
        PUBLIC_SAFETY = "Public Safety", "Public Safety"
        RECREATION = "Recreation", "Recreation"
        WORKFORCE_DEVELOPMENT = "Workforce Development", "Workforce Development"
        YOUTH_DEVELOPMENT = "Youth Development", "Youth Development"

    class OrgType(models.TextChoices):
        C501C3 = "501c3", "501(c)(3)"
        GOVERNMENT = "government", "Government"
        SCHOOL = "school", "School"
        OTHER = "other", "Other"

    user = models.OneToOneField(
        GrantUser,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    organization = models.CharField(max_length=255, blank=True)
    role_title = models.CharField(max_length=120, blank=True)

    # Intake fields (collected on first login / editable in Settings)
    title = models.CharField(max_length=255, blank=True, help_text="What you seek funding for")
    description = models.TextField(blank=True, help_text="What the work does")
    priority_area = models.CharField(
        max_length=64,
        choices=PriorityArea.choices,
        blank=True,
    )
    ntee_code = models.CharField(max_length=32, blank=True)
    location_city = models.CharField(max_length=120, blank=True)
    location_state = models.CharField(max_length=2, blank=True)
    org_type = models.CharField(max_length=32, choices=OrgType.choices, blank=True)
    budget_requested = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    eligibility_notes = models.TextField(blank=True)
    weekly_digest_enabled = models.BooleanField(
        default=True,
        help_text="Send this user the Monday grant digest email.",
    )
    onboarding_completed = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "grant_profile"
        ordering = ["-created_at"]
        verbose_name = "Grant profile"
        verbose_name_plural = "Grant profiles"

    def __str__(self) -> str:
        return f"Profile<{self.user.get_username()}>"

    @property
    def needs_onboarding(self) -> bool:
        return not self.onboarding_completed


class SavedGrant(models.Model):
    """User-saved grant opportunity stored in Supabase."""

    class Source(models.TextChoices):
        GRANTS_GOV = "grants_gov", "Grants.gov"
        USASPENDING = "usaspending", "USASpending"
        GRANTED_AI = "granted_ai", "GrantedAI"

    user = models.ForeignKey(
        GrantUser,
        on_delete=models.CASCADE,
        related_name="saved_grants",
    )
    source = models.CharField(max_length=32, choices=Source.choices)
    external_id = models.CharField(max_length=255, blank=True)
    title = models.CharField(max_length=500)
    agency = models.CharField(max_length=255, blank=True)
    agency_code = models.CharField(max_length=64, blank=True)
    agency_address = models.CharField(max_length=500, blank=True)
    top_agency = models.CharField(max_length=255, blank=True)
    deadline = models.CharField(max_length=64, blank=True)
    url = models.CharField(max_length=1000, blank=True)
    opp_status = models.CharField(max_length=120, blank=True)
    number = models.CharField(max_length=120, blank=True)
    amount = models.CharField(max_length=64, blank=True)
    category = models.CharField(max_length=64, blank=True)
    score = models.FloatField(null=True, blank=True)
    reason = models.TextField(blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "grant_saved"
        ordering = ["-created_at"]
        verbose_name = "Saved grant"
        verbose_name_plural = "Saved grants"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "source", "external_id"],
                name="uniq_user_source_external_grant",
            )
        ]

    def __str__(self) -> str:
        return f"{self.title[:60]} ({self.source})"

    @property
    def chance_percent(self) -> int:
        if self.score is None:
            return 0
        try:
            return max(0, min(100, int(round(float(self.score) * 100))))
        except (TypeError, ValueError):
            return 0


class WeeklyDigestLog(models.Model):
    """
    One row per user per digest week, so re-running the sender is idempotent and
    a failed week can be retried. `week_start` is the Monday (UTC) of the week
    the digest covers.
    """

    class Status(models.TextChoices):
        SENT = "sent", "Sent"
        SKIPPED = "skipped", "Skipped"
        FAILED = "failed", "Failed"

    user = models.ForeignKey(
        GrantUser,
        on_delete=models.CASCADE,
        related_name="weekly_digests",
    )
    week_start = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices)
    email = models.CharField(max_length=254, blank=True)
    match_count = models.PositiveIntegerField(default=0)
    webhook_status = models.PositiveIntegerField(null=True, blank=True)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "grant_weekly_digest_log"
        ordering = ["-week_start", "user_id"]
        verbose_name = "Weekly digest log"
        verbose_name_plural = "Weekly digest logs"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "week_start"],
                name="uniq_user_week_digest",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user.get_username()} {self.week_start} ({self.status})"


class GrantFeedback(models.Model):
    """
    A user's verdict on one recommended opportunity.

    Grant writers reported that most results turn out ineligible only after
    they dig in. Capturing that verdict lets the next search suppress the exact
    opportunity and down-rank the funders/categories they keep rejecting.

    Signal fields (agency/category/state) are denormalized on purpose: the row
    must stay useful for ranking even after the opportunity leaves the API.
    """

    class Verdict(models.TextChoices):
        GOOD_MATCH = "good_match", "Eligible / good match"
        NOT_ELIGIBLE = "not_eligible", "Not eligible"
        IRRELEVANT = "irrelevant", "Irrelevant"

    user = models.ForeignKey(
        GrantUser,
        on_delete=models.CASCADE,
        related_name="grant_feedback",
    )
    source = models.CharField(max_length=32)
    external_id = models.CharField(max_length=255, blank=True)
    verdict = models.CharField(max_length=32, choices=Verdict.choices)
    # The chat asks for a reason with every verdict; the weekly instruction
    # update (services/instruction_learning.py) learns from these.
    REASON_MIN_CHARS = 3
    REASON_MAX_CHARS = 1000
    note = models.TextField(blank=True, help_text="The reason the user gave for this verdict")

    # Denormalized ranking signals.
    title = models.CharField(max_length=500, blank=True)
    agency = models.CharField(max_length=255, blank=True)
    category = models.CharField(max_length=120, blank=True)
    pop_state = models.CharField(max_length=32, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "grant_feedback"
        ordering = ["-created_at"]
        verbose_name = "Grant feedback"
        verbose_name_plural = "Grant feedback"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "source", "external_id"],
                name="uniq_user_source_external_feedback",
            )
        ]
        indexes = [
            models.Index(fields=["user", "verdict"], name="idx_feedback_user_verdict"),
        ]

    def __str__(self) -> str:
        return f"{self.verdict}: {self.title[:50]}"

    @property
    def is_negative(self) -> bool:
        return self.verdict in {self.Verdict.NOT_ELIGIBLE, self.Verdict.IRRELEVANT}


class AgentSystemPrompt(models.Model):
    """
    One version of the agent's full system prompt.

    Exactly one version is active; `services.grant_agent.load_agent_instructions()`
    gives it to the agent. Version 1 is grant_agent_instructions.md as it was. The
    weekly job adds a version only where feedback conflicts with the active one,
    patching just those sentences (services/instruction_learning.py). Versions are
    kept, so any change can be reviewed, edited or rolled back in admin.
    """

    class Source(models.TextChoices):
        FILE = "file", "Imported from grant_agent_instructions.md"
        WEEKLY = "weekly", "Weekly feedback patch"
        MANUAL = "manual", "Edited in admin"

    version = models.PositiveIntegerField(unique=True)
    content = models.TextField(help_text="The full system prompt, in Markdown.")
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    based_on = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    change_summary = models.TextField(
        blank=True, help_text="What changed from the version it is based on."
    )
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "grant_agent_system_prompt"
        ordering = ["-version"]
        verbose_name = "Agent system prompt"
        verbose_name_plural = "Agent system prompts"
        constraints = [
            models.UniqueConstraint(
                fields=["is_active"],
                condition=models.Q(is_active=True),
                name="uniq_active_agent_system_prompt",
            )
        ]

    def __str__(self) -> str:
        return f"v{self.version}{' (active)' if self.is_active else ''}"


class AgentInstructionUpdate(models.Model):
    """
    Log of one weekly run that checked feedback reasons against the agent's
    system prompt.

    Applied runs patched the prompt and point at the version they created;
    skipped runs say why nothing changed. The Feedback page reads these rows to
    show which reasons were converted into instructions.
    """

    class Status(models.TextChoices):
        APPLIED = "applied", "Applied"
        SKIPPED = "skipped", "Skipped"

    class Method(models.TextChoices):
        AI = "ai", "AI"
        SUMMARY = "summary", "Pattern summary (older runs)"

    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    feedback_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices)
    method = models.CharField(max_length=16, choices=Method.choices, blank=True)
    guidance = models.TextField(
        blank=True,
        help_text="What this run changed, one '- ' line per change.",
    )
    detail = models.TextField(blank=True)
    prompt = models.ForeignKey(
        AgentSystemPrompt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updates",
        help_text="The prompt version this run created.",
    )
    conflict_feedback_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="Feedback rows whose reasons caused this run's changes.",
    )
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "grant_agent_instruction_update"
        ordering = ["-created_at"]
        verbose_name = "Agent instruction update"
        verbose_name_plural = "Agent instruction updates"

    def __str__(self) -> str:
        return f"{self.get_status_display()} {self.period_end:%Y-%m-%d}"

    def rules(self) -> list[str]:
        return [
            line[2:].strip()
            for line in (self.guidance or "").splitlines()
            if line.startswith("- ") and line[2:].strip()
        ]


class StarterPrompt(models.Model):
    """
    Customizable predefined chat starter cards ("Find grants", "Update my
    project", "View saved", "Ask anything").

    Rows are global config (not per-user) and are meant to be edited via SQL or
    Django admin. `title`/`description` set the text shown on the card. For a
    SEARCH card, `query` is the exact text handed to the matcher on top of the
    saved profile — identical to the user typing that text into the composer, so
    the existing profile + override logic is unchanged.
    """

    class Action(models.TextChoices):
        SEARCH = "search", "Search grants"
        UPDATE_PROJECT = "update_project", "Update project"
        LINK = "link", "Open link"
        FOCUS_INPUT = "focus_input", "Focus composer"

    key = models.SlugField(
        max_length=64,
        unique=True,
        help_text="Stable identifier, e.g. find_grants.",
    )
    title = models.CharField(max_length=120, help_text="Card heading shown to the user.")
    description = models.CharField(
        max_length=200,
        blank=True,
        help_text="Card subtitle shown under the heading.",
    )
    action = models.CharField(
        max_length=32,
        choices=Action.choices,
        default=Action.SEARCH,
        help_text="What clicking the card does.",
    )
    query = models.CharField(
        max_length=500,
        blank=True,
        help_text="SEARCH cards only: exact text passed to the matcher with the saved profile.",
    )
    href = models.CharField(
        max_length=300,
        blank=True,
        help_text="LINK cards only: URL to open (e.g. /accounts/saved/).",
    )
    position = models.PositiveIntegerField(default=0, help_text="Sort order (ascending).")
    is_active = models.BooleanField(default=True, help_text="Uncheck to hide the card.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "grant_starter_prompt"
        ordering = ["position", "id"]
        verbose_name = "Starter prompt"
        verbose_name_plural = "Starter prompts"

    def __str__(self) -> str:
        return f"{self.title} ({self.action})"


class Conversation(models.Model):
    """A ChatGPT-style chat thread owned by a user, optionally linked to a Project."""

    user = models.ForeignKey(
        GrantUser,
        on_delete=models.CASCADE,
        related_name="conversations",
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="conversations",
    )
    title = models.CharField(max_length=255, default="New chat")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "grant_conversation"
        ordering = ["-updated_at", "-created_at"]
        verbose_name = "Conversation"
        verbose_name_plural = "Conversations"

    def __str__(self) -> str:
        return f"{self.title} ({self.user.get_username()})"


class Message(models.Model):
    """One message in a conversation (user or assistant)."""

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField()
    # Optional structured payload (e.g. match cards) for history restore.
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "grant_message"
        ordering = ["created_at", "id"]
        verbose_name = "Message"
        verbose_name_plural = "Messages"

    def __str__(self) -> str:
        return f"{self.role}: {self.content[:48]}"
