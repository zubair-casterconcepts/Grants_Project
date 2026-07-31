import json
from argparse import ArgumentParser
from types import SimpleNamespace

from django.core.management.base import BaseCommand

from apps.projects.models import Project
from services.grants_gov import search_opportunities


class Command(BaseCommand):
    help = "Call Grants.gov search2 with a Project id or sample values; print JSON."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--project-id",
            type=int,
            default=None,
            help="Optional grant_project id to search with",
        )

    def handle(self, *args, **options):
        project_id = options.get("project_id")
        if project_id is not None:
            project = Project.objects.filter(pk=project_id).first()
            if project is None:
                self.stderr.write(self.style.ERROR(f"No Project with id={project_id}"))
                return
            subject = project
            self.stdout.write(
                f"Searching with Project #{project.id}: {project.title!r} "
                f"/ {project.priority_area!r}"
            )
        else:
            subject = SimpleNamespace(
                title="community health workforce training",
                description=(
                    "Expand access to community health worker training and "
                    "placement in underserved neighborhoods."
                ),
                priority_area="Health",
                location_city="New York",
                location_state="NY",
            )
            self.stdout.write(
                "Searching with hardcoded sample values "
                f"(title={subject.title!r}, priority_area={subject.priority_area!r}, "
                f"location={subject.location_city!r}, {subject.location_state!r})"
            )

        results = search_opportunities(subject)
        self.stdout.write(json.dumps(results, indent=2))
        self.stdout.write(self.style.SUCCESS(f"\n{len(results)} result(s)"))
