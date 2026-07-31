from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .forms import ProjectForm
from .models import Project


@login_required
@require_http_methods(["GET", "POST"])
def project_intake(request):
    if request.method == "POST":
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save(commit=False)
            project.user = request.user
            project.ntee_code = ""
            project.save()
            return redirect("projects:results", project_id=project.pk)
    else:
        form = ProjectForm()

    return render(request, "projects/intake.html", {"form": form})


@login_required
@require_http_methods(["GET"])
def project_results(request, project_id):
    project = get_object_or_404(Project, pk=project_id, user=request.user)
    return render(request, "projects/results.html", {"project": project})
