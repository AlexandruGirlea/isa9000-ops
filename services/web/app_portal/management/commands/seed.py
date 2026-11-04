from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from core.dev import ENGINEERING, PEOPLE, SALES
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from opentelemetry import trace

from app_portal.models import Course, EntraGroup, Profile
from app_portal.runs import new_run
from shared import storage, telemetry


class Command(BaseCommand):
    def handle(self, *args, **options):
        if settings.AUTH_MODE != "dev" or not settings.DEBUG:
            raise CommandError("Demo seeding requires ISA9000_AUTH=dev and DEBUG=true.")
        telemetry.setup("isa9000-web")
        with trace.get_tracer(__name__).start_as_current_span("seed"):
            with transaction.atomic(), tempfile.TemporaryDirectory() as directory:
                for object_id, name in (
                    (SALES, "Demo \u2013 Sales"),
                    (ENGINEERING, "Demo \u2013 Engineering"),
                ):
                    EntraGroup.objects.update_or_create(
                        object_id=object_id, defaults={"name": name}
                    )
                for key, person in PEOPLE.items():
                    user, _ = get_user_model().objects.get_or_create(
                        username=person["oid"],
                        defaults={
                            "first_name": person["name"],
                            "email": person["email"],
                        },
                    )
                    profile, _ = Profile.objects.get_or_create(
                        user=user,
                        defaults={
                            "role": person["role"],
                        },
                    )
                    groups = list(person["groups"])
                    if key == "admin" and settings.ADMIN_GROUP_ID:
                        EntraGroup.objects.get_or_create(
                            object_id=settings.ADMIN_GROUP_ID,
                            defaults={"name": "Demo \u2013 Administrators"},
                        )
                        groups.append(settings.ADMIN_GROUP_ID)
                    profile.groups.set(groups)
                creator = get_user_model().objects.get(username=PEOPLE["creator"]["oid"])
                course, created = Course.objects.get_or_create(
                    id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                    defaults={"group_id": SALES, "created_by": creator, "status": "ready"},
                )
                if not created and (
                    course.html or course.runs.filter(status__in=["queued", "running"]).exists()
                ):
                    self.stdout.write("Demo course already exists.")
                    return
                stub = settings.BASE_DIR.parent / "tests" / "stub_isa9000.py"
                subprocess.run(
                    [
                        sys.executable,
                        str(stub),
                        "--progress",
                        "json",
                        "create",
                        "--request",
                        "Welcome to the demo",
                        "--source",
                        "https://example.com/handbook",
                        "--audio-kb",
                        directory,
                    ],
                    env={**os.environ, "ISA9000_STUB_EXIT": "0"},
                    check=True,
                    capture_output=True,
                )
                folder = Path(directory) / "Demo course"
                manifest = json.loads((folder / "podcast.json").read_text())
                storage.upload_folder(folder, f"courses/{course.id}")
                course.manifest, course.title = manifest, manifest["title"]
                course.description, course.cover = manifest["description"], manifest["cover"]
                course.save()
                run = new_run(course, creator, "render")
            self.stdout.write(f"Demo course queued for rendering: {run.id}")
