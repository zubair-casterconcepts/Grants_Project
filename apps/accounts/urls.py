from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("onboarding/", views.onboarding_view, name="onboarding"),
    path("settings/", views.settings_view, name="settings"),
    path("profile/", views.profile_view, name="profile"),
    path("saved/", views.saved_grants_view, name="saved_grants"),
    path("saved/add/", views.save_grant_view, name="save_grant"),
    path("saved/<int:saved_id>/remove/", views.unsave_grant_view, name="unsave_grant"),
]
