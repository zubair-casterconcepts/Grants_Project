from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import GrantUser, Profile, SavedGrant


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
    list_filter = ("onboarding_completed", "priority_area", "org_type", "location_state")
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

