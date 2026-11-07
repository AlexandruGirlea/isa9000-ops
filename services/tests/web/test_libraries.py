from __future__ import annotations

import importlib
import runpy
import uuid
from pathlib import Path
from unittest.mock import patch

from app_portal.models import Course, EntraGroup, Profile
from django.apps import apps
from django.contrib.auth import get_user_model
from django.utils import timezone

from .helpers import (
    ENGINEERING,
    OTHER,
    OVERAGE,
    SALES,
    LibraryCase,
    administrator,
    principal,
    token,
    viewer,
)


class VisibilityTableTests(LibraryCase):
    def test_hidden_groups_are_hidden_everywhere(self):
        hidden = EntraGroup.objects.create(object_id=uuid.uuid4(), name="Directory only")
        for headers in (
            token(groups=[str(hidden.pk)]),
            viewer([str(hidden.pk)]),
            administrator(),
        ):
            self.assertFalse(self.listed(headers, hidden.pk))
            self.assertEqual(self.status(f"/libraries/{hidden.pk}/", headers), 404)
            self.assertEqual(self.status(f"/libraries/{hidden.pk}/new/", headers), 404)
        for headers in (token(groups=[str(hidden.pk)]), administrator()):
            self.assertNotContains(self.client.get("/libraries/new/", **headers), "Directory only")

    def test_enabled_library_with_content_follows_each_role(self):
        Course.objects.create(group=self.sales, created_by=self.user, title="Quiet draft")
        for headers, draft_visible in (
            (viewer(), False), (self.headers, True), (administrator(), True),
        ):
            self.assertTrue(self.listed(headers))
            page = self.client.get(f"/libraries/{SALES}/", **headers).content.decode()
            self.assertIn("Sales course", page)
            self.assertEqual("Quiet draft" in page, draft_visible)

    def test_only_drafts_stay_hidden_from_viewers(self):
        self.course.delete()
        Course.objects.create(group=self.sales, created_by=self.user, title="Quiet draft")
        self.assertFalse(self.listed(viewer()))
        self.assertEqual(self.status(f"/libraries/{SALES}/", viewer()), 404)
        self.assertTrue(self.listed(self.headers))

    def test_empty_library_shows_to_creators_and_admins_only(self):
        self.assertFalse(self.listed(viewer([OTHER]), OTHER))
        for path in (f"/libraries/{OTHER}/", f"/libraries/{OTHER}/new/"):
            self.assertEqual(self.status(path, viewer([OTHER])), 404)
            for headers in (token(groups=[OTHER]), administrator()):
                self.assertTrue(self.listed(headers, OTHER))
                self.assertEqual(self.status(path, headers), 200)

    def test_published_shared_podcast_counts_but_a_shared_draft_does_not(self):
        self.course.shared_groups.add(self.empty)
        self.assertTrue(self.listed(viewer([OTHER]), OTHER))
        self.assertEqual(self.status(f"/libraries/{OTHER}/", viewer([OTHER])), 200)
        self.course.status = "draft"
        self.course.save()
        self.assertFalse(self.listed(viewer([OTHER]), OTHER))

    def test_hiding_a_library_revokes_every_portal_path_and_keeps_data(self):
        self.course.shared_groups.add(self.empty)
        self.sales.status = "hidden"
        self.sales.save()
        for headers in (self.headers, viewer(), viewer([OTHER]), administrator()):
            self.assertEqual(self.status(f"/courses/{self.course.id}/", headers), 404)
            self.assertEqual(
                self.status(f"/media/{self.course.id}/01 - First.mp3", headers), 404,
            )
            self.assertEqual(self.status(f"/libraries/{SALES}/", headers), 404)
        self.assertEqual(self.status(f"/courses/{self.course.id}/manage/", self.headers), 404)
        self.assertEqual(self.status(f"/runs/{self.run.id}/", self.headers), 404)
        self.assertEqual(self.status(f"/libraries/{SALES}/new/", self.headers), 404)
        self.assertFalse(self.listed(viewer([OTHER]), OTHER))
        self.assertTrue(Course.objects.filter(pk=self.course.pk, title="Sales course").exists())
        self.sales.status = "enabled"
        self.sales.save()
        self.assertEqual(self.status(f"/courses/{self.course.id}/", self.headers), 200)

    def test_hidden_shared_library_stops_being_a_path_of_access(self):
        self.course.shared_groups.add(self.engineering)
        self.engineering.status = "hidden"
        self.engineering.save()
        self.assertEqual(self.status(f"/courses/{self.course.id}/", viewer([ENGINEERING])), 404)

class DirectoryClaimTests(LibraryCase):
    def test_largest_group_list_fits_the_web_server_header_limit(self):
        config = Path(__file__).resolve().parents[2] / "web" / "gunicorn.conf.py"
        limit = runpy.run_path(str(config))["limit_request_field_size"]
        header = principal(groups=[str(uuid.uuid4()) for _ in range(200)])
        self.assertGreater(len(header), 8190)
        self.assertLess(len(header), limit)

    def test_overage_tokens_sign_in_without_following_claim_sources(self):
        cases = (OVERAGE, {"hasgroups": True}, {**OVERAGE, "groups": [SALES]})
        for claims in cases:
            with self.subTest(claims=sorted(claims)), patch(
                "requests.sessions.Session.request",
            ) as request:
                oid = str(uuid.uuid4())
                response = self.client.get("/libraries/", **token(oid, **claims))
                self.assertContains(response, "local library access")
                self.assertContains(response, oid)
                profile = Profile.objects.get(user__username=oid)
                self.assertEqual(profile.membership, "overage")
                self.assertFalse(profile.groups.exists())
                self.assertEqual(self.status(f"/libraries/{SALES}/", token(oid, **claims)), 404)
                request.assert_not_called()

    def test_missing_and_malformed_claims_are_unresolved_not_empty(self):
        for groups, membership in (
            (None, "missing"), ("not-a-list", "failed"), ([1], "failed"),
            (["not-a-group"], "failed"), ({"a": 1}, "failed"),
        ):
            with self.subTest(groups=groups):
                oid = str(uuid.uuid4())
                claims = {} if groups is None else {"groups": groups}
                headers = {"HTTP_X_MS_TOKEN_AAD_ID_TOKEN": principal(oid, **claims)}
                self.assertContains(
                    self.client.get("/libraries/", **headers), "local library access",
                )
                self.assertEqual(
                    Profile.objects.get(user__username=oid).membership, membership,
                )

    def test_invalid_identity_tokens_are_refused_and_create_nothing(self):
        oid = str(uuid.uuid4())
        for claims in (
            {"tid": str(uuid.uuid4())},
            {"iss": "https://login.microsoftonline.com/other/v2.0"},
            {"aud": str(uuid.uuid4())},
            {"nbf": int(timezone.now().timestamp()) + 3600},
        ):
            with self.subTest(claims=sorted(claims)):
                self.assertEqual(
                    self.status("/libraries/", token(oid, [SALES], **claims)), 403,
                )
        self.assertEqual(self.status("/libraries/", token(oid, [SALES], exp=1)), 302)
        self.assertFalse(get_user_model().objects.filter(username=oid).exists())


class MigrationTests(LibraryCase):
    def test_existing_groups_are_classified_without_losing_anything(self):
        receiving = EntraGroup.objects.create(object_id=uuid.uuid4(), name="Receiving")
        draft_owner = EntraGroup.objects.create(object_id=uuid.uuid4())
        idle_named = EntraGroup.objects.create(object_id=uuid.uuid4(), name="Idle")
        idle_unnamed = EntraGroup.objects.create(object_id=uuid.uuid4())
        self.course.shared_groups.add(receiving)
        Course.objects.create(group=draft_owner, created_by=self.user)
        self.profile.groups.add(idle_unnamed)
        EntraGroup.objects.update(status="hidden")
        before = EntraGroup.objects.count()
        migration = importlib.import_module("app_portal.migrations.0002_library_access")
        migration.enable_libraries_with_podcasts(apps, None)
        migration.name_groups_after_their_id(apps, None)
        idle_unnamed.refresh_from_db()
        draft_owner.refresh_from_db()
        idle_named.refresh_from_db()
        self.assertEqual(idle_unnamed.name, str(idle_unnamed.pk))
        self.assertEqual(draft_owner.name, str(draft_owner.pk))
        self.assertEqual(idle_named.name, "Idle")
        status = dict(EntraGroup.objects.values_list("pk", "status"))
        self.assertEqual(EntraGroup.objects.count(), before)
        for group in (self.sales, receiving, draft_owner):
            self.assertEqual(status[group.pk], "enabled")
        for group in (idle_named, idle_unnamed, self.engineering, self.empty):
            self.assertEqual(status[group.pk], "hidden")
        self.assertTrue(self.profile.groups.filter(pk=idle_unnamed.pk).exists())
