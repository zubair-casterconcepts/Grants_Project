from django.urls import path

from . import views

app_name = "projects"

urlpatterns = [
    path("new/", views.project_intake, name="intake"),
    path("<int:project_id>/results/", views.project_results, name="results"),
]
