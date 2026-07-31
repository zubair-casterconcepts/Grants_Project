from django.conf import settings
from django.db import models


class Project(models.Model):
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

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="projects",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(help_text="What the project does")
    priority_area = models.CharField(max_length=64, choices=PriorityArea.choices)
    ntee_code = models.CharField(max_length=32, blank=True)
    location_city = models.CharField(max_length=120)
    location_state = models.CharField(max_length=2)
    org_type = models.CharField(max_length=32, choices=OrgType.choices)
    budget_requested = models.DecimalField(max_digits=14, decimal_places=2)
    eligibility_notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "grant_project"
        ordering = ["-created_at"]
        verbose_name = "Project"
        verbose_name_plural = "Projects"

    def __str__(self) -> str:
        return self.title


class MatchResult(models.Model):
    class Source(models.TextChoices):
        GRANTS_GOV = "grants_gov", "Grants.gov"
        USASPENDING = "usaspending", "USASpending"
        PROPUBLICA = "propublica", "ProPublica"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        CONFIRMED = "confirmed", "Confirmed"
        REJECTED = "rejected", "Rejected"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="matches",
    )
    funder_name = models.CharField(max_length=255)
    source = models.CharField(max_length=32, choices=Source.choices)
    score = models.FloatField()
    reasoning = models.TextField()
    raw_data = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "grant_match_result"
        ordering = ["-created_at"]
        verbose_name = "Match result"
        verbose_name_plural = "Match results"

    def __str__(self) -> str:
        return f"{self.funder_name} → {self.project_id}"
