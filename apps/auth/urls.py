from django.urls import path

from . import views

app_name = "auth"

urlpatterns = [
    path("", views.login_view, name="login"),
    path("login/", views.login_view, name="login_alt"),
    path("logout/", views.logout_view, name="logout"),
    path("home/", views.home_view, name="home"),
    path("home/matches/", views.matches_api_view, name="home_matches"),
    path("home/matches/stream/", views.matches_stream_api_view, name="home_matches_stream"),
    path("home/chat/profile/", views.chat_profile_api, name="chat_profile"),
    path("home/conversations/", views.conversations_api_view, name="conversations"),
    path(
        "home/conversations/<int:conversation_id>/",
        views.conversation_detail_api,
        name="conversation_detail",
    ),
    path(
        "home/conversations/<int:conversation_id>/messages/",
        views.conversation_messages_api,
        name="conversation_messages",
    ),
    path(
        "home/conversations/<int:conversation_id>/sync-project/",
        views.conversation_sync_project_api,
        name="conversation_sync_project",
    ),
]
