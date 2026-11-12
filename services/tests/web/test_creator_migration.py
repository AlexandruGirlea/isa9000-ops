from __future__ import annotations

import uuid
from datetime import timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class CreatorMigrationTests(TransactionTestCase):
    old = (("app_portal", "0002_library_access"),)
    new = (("app_portal", "0003_library_creators_run_episodes"),)

    def test_upgrade_preserves_data_and_only_assigns_existing_creator_libraries(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.old)
        self.addCleanup(self.restore_schema)
        old_apps = executor.loader.project_state(self.old).apps
        User = old_apps.get_model("auth", "User")
        Profile = old_apps.get_model("app_portal", "Profile")
        Group = old_apps.get_model("app_portal", "EntraGroup")
        Grant = old_apps.get_model("app_portal", "LibraryGrant")
        Course = old_apps.get_model("app_portal", "Course")
        Run = old_apps.get_model("app_portal", "Run")
        user = User.objects.create(username=str(uuid.uuid4()))
        creator = Profile.objects.create(user=user, role="creator", membership="complete")
        viewer = Profile.objects.create(
            user=User.objects.create(username=str(uuid.uuid4())), membership="complete",
        )
        unknown = Profile.objects.create(
            user=User.objects.create(username=str(uuid.uuid4())), role="creator",
            membership="overage",
        )
        libraries = [
            Group.objects.create(object_id=uuid.uuid4(), name=str(i), status=status)
            for i, status in enumerate(["enabled"] * 4 + ["hidden"])
        ]
        creator.groups.add(libraries[0], libraries[4])
        viewer.groups.add(libraries[0])
        unknown.groups.add(libraries[0])
        for library, extra in (
            (libraries[0], {}),  # Duplicate membership is assigned just once.
            (libraries[1], {}),
            (libraries[2], {"expires": timezone.now() - timedelta(days=1)}),
            (libraries[3], {"revoked": timezone.now(), "revoked_by": user}),
        ):
            Grant.objects.create(profile=creator, library=library, granted_by=user, **extra)
        course = Course.objects.create(group=libraries[0], created_by=user, title="Existing")
        Run.objects.create(course=course, requested_by=user, task="create", correlation_id="a")

        executor = MigrationExecutor(connection)
        executor.migrate(self.new)
        new_apps = executor.loader.project_state(self.new).apps
        NewProfile = new_apps.get_model("app_portal", "Profile")
        upgraded = NewProfile.objects.get(pk=creator.pk)
        self.assertEqual(set(upgraded.creator_libraries.values_list("pk", flat=True)), {
            libraries[0].pk, libraries[1].pk,
        })
        self.assertEqual(upgraded.role, "creator")
        for profile in (viewer, unknown):
            self.assertFalse(NewProfile.objects.get(pk=profile.pk).creator_libraries.exists())
        self.assertEqual(new_apps.get_model("app_portal", "LibraryGrant").objects.count(), 4)
        NewRun = new_apps.get_model("app_portal", "Run")
        self.assertIsNone(NewRun.objects.get().episodes)
        # Old web replicas can still insert their old columns while the upgrade rolls out.
        Run.objects.create(course=course, requested_by=user, task="create", correlation_id="b")
        self.assertEqual(NewRun.objects.filter(episodes__isnull=True).count(), 2)

        executor = MigrationExecutor(connection)
        executor.migrate(self.old)
        self.assertEqual(Profile.objects.get(pk=creator.pk).role, "creator")
        self.assertEqual(Grant.objects.count(), 4)
        self.assertEqual(Course.objects.get().title, "Existing")
        self.assertEqual(Run.objects.count(), 2)

    def restore_schema(self):
        MigrationExecutor(connection).migrate(self.new)
