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
