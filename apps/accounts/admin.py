from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (
    AgentInstructionUpdate,
    AgentSystemPrompt,
    Conversation,
    GrantFeedback,
    GrantUser,
    Message,
    Profile,
    SavedGrant,
    StarterPrompt,
    WeeklyDigestLog,
)


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    fk_name = "user"
    extra = 0
    fields = (
        "organization",
        "role_title",
        "title",
        "description",
        "priority_area",
        "ntee_code",
        "location_city",
        "location_state",
        "org_type",
        "budget_requested",
        "eligibility_notes",
        "weekly_digest_enabled",
        "onboarding_completed",
    )


@admin.register(GrantUser)
class GrantUserAdmin(DjangoUserAdmin):
    inlines = [ProfileInline]
    list_display = ("username", "email", "first_name", "last_name", "is_staff", "is_active")
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering = ("username",)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "organization",
        "priority_area",
        "org_type",
        "location_city",
        "location_state",
        "onboarding_completed",
        "updated_at",
    )
    list_filter = (
        "onboarding_completed",
        "weekly_digest_enabled",
        "priority_area",
        "org_type",
        "location_state",
    )
    search_fields = (
        "user__username",
        "user__email",
        "organization",
        "title",
        "role_title",
    )
    autocomplete_fields = ("user",)


@admin.register(SavedGrant)
class SavedGrantAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "source", "agency", "score", "created_at")
    list_filter = ("source", "created_at")
    search_fields = ("title", "agency", "number", "external_id", "user__username")
    autocomplete_fields = ("user",)


@admin.register(WeeklyDigestLog)
class WeeklyDigestLogAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "week_start",
        "status",
        "email",
        "match_count",
        "webhook_status",
        "updated_at",
    )
    list_filter = ("status", "week_start")
    search_fields = ("user__username", "email", "detail")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    fields = ("role", "content", "created_at")
    readonly_fields = ("created_at",)
    show_change_link = True


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "project", "updated_at", "created_at")
    list_filter = ("created_at", "updated_at")
    search_fields = ("title", "user__username", "project__title")
    autocomplete_fields = ("user", "project")
    readonly_fields = ("created_at", "updated_at")
    inlines = [MessageInline]


@admin.register(GrantFeedback)
class GrantFeedbackAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "verdict", "short_reason", "agency", "category", "created_at")
    list_filter = ("verdict", "source", "created_at")
    search_fields = ("title", "note", "agency", "category", "external_id", "user__username")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Reason")
    def short_reason(self, obj: GrantFeedback) -> str:
        return (obj.note or "")[:80]


class AgentSystemPromptForm(forms.ModelForm):
    class Meta:
        model = AgentSystemPrompt
        fields = "__all__"

    def _get_validation_exclusions(self):
        excluded = super()._get_validation_exclusions()
        # "One active version" is kept by save_model, which switches the current
        # one off first; validating it here would reject every activation.
        excluded.add("is_active")
        return excluded


@admin.register(AgentSystemPrompt)
class AgentSystemPromptAdmin(admin.ModelAdmin):
    """
    The agent's full system prompt, versioned. Exactly one version is active.
    Saving a change or adding a version takes effect within minutes; use the
    action to roll back to an older version.
    """

    form = AgentSystemPromptForm
    list_display = ("version", "source", "is_active", "created_at", "short_summary")
    list_filter = ("source", "is_active")
    ordering = ("-version",)
    fields = ("version", "is_active", "source", "based_on", "change_summary", "content", "created_at")
    readonly_fields = ("version", "source", "based_on", "created_at")
    actions = ("activate_version",)

    @admin.display(description="Changes")
    def short_summary(self, obj: AgentSystemPrompt) -> str:
        return " ".join((obj.change_summary or "").split())[:90]

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        field = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == "content" and field is not None:
            field.widget.attrs.update(rows=40, style="width: 100%; font-family: monospace;")
        return field

    def save_model(self, request, obj, form, change):
        from django.db import transaction
        from django.db.models import Max

        from services.instruction_learning import (
            activate_prompt,
            clear_guidance_cache,
            normalize_prompt,
        )

        obj.content = normalize_prompt(obj.content)
        with transaction.atomic():
            if not obj.pk:
                top = AgentSystemPrompt.objects.aggregate(top=Max("version"))["top"] or 0
                obj.version = top + 1
                obj.source = AgentSystemPrompt.Source.MANUAL
                obj.based_on = AgentSystemPrompt.objects.filter(is_active=True).first()
            wants_active = obj.is_active
            obj.is_active = False  # only one active version: switch the others off first
            super().save_model(request, obj, form, change)
            if wants_active:
                activate_prompt(obj)
        clear_guidance_cache()

    @admin.action(description="Make the selected version the active prompt")
    def activate_version(self, request, queryset):
        from services.instruction_learning import activate_prompt

        if queryset.count() != 1:
            self.message_user(request, "Select exactly one version to activate.", level="warning")
            return
        chosen = queryset.first()
        activate_prompt(chosen)
        self.message_user(request, f"Version {chosen.version} is now the active prompt.")


@admin.register(AgentInstructionUpdate)
class AgentInstructionUpdateAdmin(admin.ModelAdmin):
    """Read-only log of weekly runs. The prompt itself is under Agent system prompts."""

    list_display = ("created_at", "period_end", "feedback_count", "status", "method", "prompt", "is_active")
    list_filter = ("status", "method", "is_active")
    fields = (
        "status",
        "method",
        "prompt",
        "is_active",
        "feedback_count",
        "period_start",
        "period_end",
        "guidance",
        "detail",
        "conflict_feedback_ids",
        "created_at",
    )
    readonly_fields = fields

    def has_add_permission(self, request):
        return False


@admin.register(StarterPrompt)
class StarterPromptAdmin(admin.ModelAdmin):
    list_display = ("title", "action", "query", "href", "position", "is_active", "updated_at")
    list_filter = ("action", "is_active")
    list_editable = ("action", "position", "is_active")
    search_fields = ("key", "title", "description", "query")
    ordering = ("position", "id")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "role", "short_content", "created_at")
    list_filter = ("role", "created_at")
    search_fields = ("content", "conversation__title", "conversation__user__username")
    autocomplete_fields = ("conversation",)
    readonly_fields = ("created_at",)

    @admin.display(description="Content")
    def short_content(self, obj: Message) -> str:
        return (obj.content or "")[:80]

