from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (
    AgentInstructionUpdate,
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


@admin.register(AgentInstructionUpdate)
class AgentInstructionUpdateAdmin(admin.ModelAdmin):
    """Weekly learned rules. Only one update is active; edits apply within minutes."""

    list_display = ("created_at", "period_end", "feedback_count", "status", "method", "is_active")
    list_filter = ("status", "method", "is_active")
    fields = (
        "is_active",
        "guidance",
        "status",
        "method",
        "feedback_count",
        "period_start",
        "period_end",
        "detail",
        "created_at",
    )
    readonly_fields = (
        "status",
        "method",
        "feedback_count",
        "period_start",
        "period_end",
        "detail",
        "created_at",
    )
    actions = ("activate_update", "deactivate_updates")

    def save_model(self, request, obj, form, change):
        from services.instruction_learning import clear_guidance_cache

        if obj.is_active:
            AgentInstructionUpdate.objects.exclude(pk=obj.pk).update(is_active=False)
        super().save_model(request, obj, form, change)
        clear_guidance_cache()

    @admin.action(description="Activate the selected update (deactivates the others)")
    def activate_update(self, request, queryset):
        from services.instruction_learning import clear_guidance_cache

        chosen = queryset.filter(status=AgentInstructionUpdate.Status.APPLIED).order_by("-created_at").first()
        if chosen is None:
            self.message_user(request, "Pick an applied update to activate.", level="warning")
            return
        AgentInstructionUpdate.objects.exclude(pk=chosen.pk).update(is_active=False)
        AgentInstructionUpdate.objects.filter(pk=chosen.pk).update(is_active=True)
        clear_guidance_cache()
        self.message_user(request, f"Activated the update from {chosen.period_end:%Y-%m-%d}.")

    @admin.action(description="Deactivate the selected updates")
    def deactivate_updates(self, request, queryset):
        from services.instruction_learning import clear_guidance_cache

        count = queryset.update(is_active=False)
        clear_guidance_cache()
        self.message_user(request, f"Deactivated {count} update(s).")


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

