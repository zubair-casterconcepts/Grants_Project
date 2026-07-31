from django.contrib import admin

from .models import MatchResult, Project


class MatchResultInline(admin.TabularInline):
    model = MatchResult
    extra = 0
    fields = ("funder_name", "source", "score", "status", "created_at")
    readonly_fields = ("created_at",)
    show_change_link = True


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "user",
        "priority_area",
        "org_type",
        "location_city",
        "location_state",
        "budget_requested",
        "created_at",
    )
    list_filter = ("priority_area", "org_type", "location_state")
    search_fields = ("title", "description", "user__username", "ntee_code")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at",)
    inlines = [MatchResultInline]


@admin.register(MatchResult)
class MatchResultAdmin(admin.ModelAdmin):
    list_display = (
        "funder_name",
        "project",
        "source",
        "score",
        "status",
        "created_at",
    )
    list_filter = ("source", "status")
    search_fields = ("funder_name", "project__title", "reasoning")
    autocomplete_fields = ("project",)
    readonly_fields = ("created_at",)
