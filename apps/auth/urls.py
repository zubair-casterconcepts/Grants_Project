from django.urls import path

from . import views

app_name = "auth"

urlpatterns = [
    path("", views.login_view, name="login"),
    path("login/", views.login_view, name="login_alt"),
    path("logout/", views.logout_view, name="logout"),
    path("home/", views.home_view, name="home"),
    path("home/matches/", views.matches_api_view, name="home_matches"),
]
