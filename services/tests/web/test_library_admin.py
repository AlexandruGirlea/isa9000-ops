from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import jwt
from app_portal.library_admin import LibraryGrantForm
from app_portal.models import Course, EntraGroup, LibraryGrant, Profile, Run
from app_portal.permissions import groups_for
from core.dev import ENGINEERING, SALES
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.utils import timezone

from .helpers import (
    ADMIN_OID,
    CLIENT,
    OTHER,
    OVERAGE,
    TENANT,
    LibraryCase,
    administrator,
    token,
    viewer,
)


class PortalListingTests(LibraryCase):
    def test_token_signed_by_another_key_is_refused(self):
        forged = jwt.encode(
            {
                "oid": str(uuid.uuid4()), "tid": TENANT, "aud": CLIENT, "groups": [SALES],
                "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
                "iat": int(timezone.now().timestamp()),
                "exp": int(timezone.now().timestamp()) + 3600,
            },
            rsa.generate_private_key(public_exponent=65537, key_size=2048),
            algorithm="RS256",
        )
        self.assertEqual(self.status("/libraries/", {"HTTP_X_MS_TOKEN_AAD_ID_TOKEN": forged}), 403)

    def test_searches_narrow_each_catalogue(self):
        Course.objects.create(
            group=self.engineering, created_by=self.user, status="ready", title="Rust basics",
            html="x",
        )
        Course.objects.create(
            group=self.sales, created_by=self.user, status="ready", title="Pricing", html="x",
        )
        page = self.client.get("/libraries/", {"q": "engin"}, **token(groups=[SALES, ENGINEERING]))
        self.assertContains(page, f"/libraries/{ENGINEERING}/")
        self.assertNotContains(page, f"/libraries/{SALES}/")
        page = self.client.get(
            "/libraries/new/", {"q": "engin"}, **token(groups=[SALES, ENGINEERING]),
        )
        self.assertContains(page, f"/libraries/{ENGINEERING}/new/")
        self.assertNotContains(page, f"/libraries/{SALES}/new/")
        page = self.client.get(f"/libraries/{SALES}/", {"q": "pric"}, **self.headers)
        self.assertContains(page, "Pricing")
        self.assertNotContains(page, "Sales course")

    def test_library_podcasts_are_paginated(self):
        Course.objects.bulk_create([
            Course(group=self.sales, created_by=self.user, status="ready", title=f"Episode {index}")
            for index in range(30)
        ])
        page = self.client.get(f"/libraries/{SALES}/", **self.headers)
        self.assertEqual(page.content.count(b"col-md-6 catalog-item"), 24)
        self.assertContains(page, "Page 1 of 2")
        page = self.client.get(f"/libraries/{SALES}/", {"page": 2}, **self.headers)
        self.assertEqual(page.content.count(b"col-md-6 catalog-item"), 7)

    def test_library_card_shows_the_newest_visible_podcast_from_either_branch(self):
        self.course.shared_groups.add(self.empty)
        own = Course.objects.create(
            group=self.empty, created_by=self.user, status="ready", title="Own", cover="own.png",
        )
        Course.objects.filter(pk=own.pk).update(updated=timezone.now() - timedelta(days=9))
        page = self.client.get("/libraries/", **viewer([OTHER]))
        self.assertContains(page, f"/media/{self.course.id}/cover.png")
        self.assertContains(page, "2 podcasts")
        Course.objects.filter(pk=own.pk).update(updated=timezone.now() + timedelta(days=1))
        page = self.client.get("/libraries/", **viewer([OTHER]))
        self.assertContains(page, f"/media/{own.id}/own.png")

    def test_shared_library_page_hides_podcasts_of_an_archived_owner(self):
        self.course.shared_groups.add(self.empty)
        Course.objects.create(group=self.empty, created_by=self.user, status="ready", title="Own")
        self.sales.status = "archived"
        self.sales.save()
        page = self.client.get(f"/libraries/{OTHER}/", **viewer([OTHER]))
        self.assertContains(page, "Own")
        self.assertNotContains(page, "Sales course")

    def test_unresolved_notice_is_not_shown_to_administrators(self):
        Profile.objects.filter(pk=self.profile.pk).update(local_admin=True)
        self.assertNotContains(
            self.client.get("/libraries/", **token(**OVERAGE)), "local library access",
        )

    def test_history_drops_podcasts_from_libraries_the_person_lost(self):
        self.assertContains(self.client.get("/runs/", **self.headers), "Sales course")
        self.assertNotContains(self.client.get("/runs/", **token(groups=[])), "Sales course")

    def test_deleting_one_of_several_podcasts_returns_to_the_library(self):
        Run.objects.all().update(status="ready")
        Course.objects.create(group=self.sales, created_by=self.user, status="ready", title="Other")
        with patch("app_portal.views.storage.delete_prefix"):
            response = self.client.post(
                f"/courses/{self.course.id}/manage/", {"action": "delete_course"}, **self.headers,
            )
        self.assertEqual(response.url, f"/libraries/{SALES}/")

    def test_a_request_is_judged_by_its_own_token_not_the_stored_snapshot(self):
        self.profile.groups.set([self.engineering])
        user = get_user_model().objects.get(pk=self.user.pk)
        user.directory_groups = {self.sales.pk}
        self.assertEqual(list(groups_for(user)), [self.sales])
        user.directory_groups = set()
        self.assertEqual(list(groups_for(user)), [])


class AdminCatalogueTests(LibraryCase):
    def setUp(self):
        super().setUp()
        make = EntraGroup.objects.create
        self.discovered_idle = make(object_id=uuid.uuid4(), name="Idle")
        self.unnamed_idle = make(object_id=uuid.uuid4())
        self.unnamed_reviewed = make(object_id=uuid.uuid4(), reviewed=True)
        self.unnamed_shared = make(object_id=uuid.uuid4())
        self.draft_owner = make(object_id=uuid.uuid4(), name="Drafts")
        self.mixed_owner = make(object_id=uuid.uuid4(), name="Mixed", show_when_empty=True)
        self.draft_share = make(object_id=uuid.uuid4(), name="Draft share")
        self.course.shared_groups.add(self.unnamed_shared)
        Course.objects.create(group=self.draft_owner, created_by=self.user)
        Course.objects.create(group=self.mixed_owner, created_by=self.user)
        Course.objects.create(group=self.mixed_owner, created_by=self.user, status="ready")
        Course.objects.create(group=self.sales, created_by=self.user).shared_groups.add(
            self.draft_share,
        )

    def rows(self, path="/admin/app_portal/entragroup/", **params):
        response = self.client.get(path, params, **administrator())
        self.assertEqual(response.status_code, 200)
        return set(response.context_data["cl"].queryset.values_list("pk", flat=True))

    def test_filters_return_exactly_the_right_libraries(self):
        everything = set(EntraGroup.objects.values_list("pk", flat=True))
        no_content = {
            self.discovered_idle.pk, self.unnamed_idle.pk, self.unnamed_reviewed.pk,
            self.draft_share.pk, self.empty.pk, self.engineering.pk,
        }
        for params, expected in (
            ({}, {self.sales.pk, self.engineering.pk, self.empty.pk}),
            ({"status": "all"}, everything),
            ({"status": "archived"}, set()),
            ({"status": "review"}, {self.unnamed_idle.pk}),
            ({"status": "all", "named": "missing"}, {
                self.unnamed_idle.pk, self.unnamed_reviewed.pk, self.unnamed_shared.pk,
            }),
            ({"status": "all", "reviewed__exact": 1}, {self.unnamed_reviewed.pk}),
            ({"status": "all", "show_when_empty__exact": 1}, {self.mixed_owner.pk}),
            ({"status": "all", "content": "none"}, no_content),
            ({"status": "all", "content": "owned"}, {
                self.sales.pk, self.draft_owner.pk, self.mixed_owner.pk,
            }),
            ({"status": "all", "content": "shared"}, {self.unnamed_shared.pk}),
            ({"status": "all", "content": "drafts"}, {self.draft_owner.pk}),
        ):
            with self.subTest(params=params):
                self.assertEqual(self.rows(**params), expected)

    def test_invalid_filter_values_do_not_fail(self):
        for path, params in (
            ("/admin/app_portal/entragroup/", {"status": "nope", "named": "x", "content": "x"}),
            ("/admin/app_portal/profile/", {"library": "not-a-uuid"}),
            ("/admin/app_portal/course/", {"library": "not-a-uuid", "shared_library": "x"}),
            ("/admin/app_portal/run/", {"library": "not-a-uuid"}),
            ("/admin/app_portal/librarygrant/", {"state": "nope"}),
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    self.client.get(path, params, **administrator()).status_code, 200,
                )

    def test_mark_reviewed_is_audited(self):
        response = self.client.post(
            "/admin/app_portal/entragroup/?status=review",
            {"action": "mark_reviewed", "_selected_action": str(self.unnamed_idle.pk)},
            **administrator(),
        )
        self.assertEqual(response.status_code, 302)
        self.unnamed_idle.refresh_from_db()
        self.assertTrue(self.unnamed_idle.reviewed)
        self.assertTrue(LogEntry.objects.filter(
            object_id=str(self.unnamed_idle.pk), change_message__contains="reviewed",
        ).exists())

    def test_people_are_found_by_access_membership_and_reference(self):
        grantee = Profile.objects.create(user=self.make_user("Local Holder"))
        stale = Profile.objects.create(user=self.make_user("Stale Member"), membership="overage")
        stale.groups.add(self.engineering)
        current = Profile.objects.create(user=self.make_user("Current Member"))
        current.groups.add(self.engineering)
        LibraryGrant.objects.create(
            profile=grantee, library=self.engineering, granted_by=self.user, reason="Test",
        )
        path = "/admin/app_portal/profile/"
        self.assertEqual(self.rows(path, library=ENGINEERING), {grantee.pk, current.pk})
        self.assertEqual(self.rows(path, membership__exact="overage"), {stale.pk})
        self.assertEqual(self.rows(path, q=stale.user.username), {stale.pk})
        page = self.client.get(
            f"/admin/app_portal/entragroup/{ENGINEERING}/change/", **administrator(),
        )
        self.assertContains(page, "1 from Entra, 1 local")

    def make_user(self, name):
        return get_user_model().objects.create_user(username=str(uuid.uuid4()), first_name=name)

    def test_stale_memberships_are_not_presented_as_access(self):
        self.client.get("/libraries/", **token(**OVERAGE))
        page = self.client.get(
            f"/admin/app_portal/profile/{self.profile.pk}/change/", **administrator(),
        )
        self.assertNotContains(page, "Sales: Entra membership")
        self.assertContains(page, "No library access")
        self.client.get("/libraries/", **self.headers)
        self.user.is_active = False
        self.user.save()
        page = self.client.get(
            f"/admin/app_portal/profile/{self.profile.pk}/change/", **administrator(),
        )
        self.assertContains(page, "Suspended: no access while inactive")

    def test_revoking_the_flag_updates_the_role_shown_immediately(self):
        Profile.objects.filter(pk=self.profile.pk).update(local_admin=True)
        self.client.get("/libraries/", **self.headers)
        self.client.post(
            f"/admin/app_portal/profile/{self.profile.pk}/change/",
            {"role": "creator", "is_active": "on", "_save": "Save"}, **administrator(),
        )
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_superuser or self.user.is_staff)


class LocalAccessRecordTests(LibraryCase):
    def setUp(self):
        super().setUp()
        self.client.get("/libraries/", **administrator())
        self.grant = LibraryGrant.objects.create(
            profile=self.profile, library=self.engineering, reason="Test",
            granted_by=Profile.objects.get(user__username=ADMIN_OID).user,
        )

    def test_records_cannot_be_edited_or_deleted(self):
        change = f"/admin/app_portal/librarygrant/{self.grant.pk}/change/"
        response = self.client.post(change, {"reason": "Edited"}, **administrator())
        self.assertEqual(response.status_code, 403)
        delete = f"/admin/app_portal/librarygrant/{self.grant.pk}/delete/"
        response = self.client.post(delete, {"post": "yes"}, **administrator())
        self.assertEqual(response.status_code, 403)
        self.grant.refresh_from_db()
        self.assertEqual(self.grant.reason, "Test")

    def test_only_enabled_libraries_can_be_granted(self):
        for status in ("discovered", "archived"):
            library = EntraGroup.objects.create(
                object_id=uuid.uuid4(), name="Closed", status=status,
            )
            response = self.client.post(
                "/admin/app_portal/librarygrant/add/",
                {
                    "profile": self.profile.pk, "library": str(library.pk), "reason": "No",
                    "_save": "Save",
                },
                **administrator(),
            )
            self.assertEqual(response.status_code, 200)
            self.assertFalse(LibraryGrant.objects.filter(library=library).exists())
            results = self.client.get(
                "/admin/autocomplete/",
                {
                    "app_label": "app_portal", "model_name": "librarygrant",
                    "field_name": "library", "term": "Closed",
                },
                **administrator(),
            ).json()["results"]
            self.assertEqual(results, [])

    def test_form_uses_searchable_pickers(self):
        EntraGroup.objects.create(
            object_id=uuid.uuid4(), name="Should not render", status="enabled",
        )
        page = self.client.get("/admin/app_portal/librarygrant/add/", **administrator())
        self.assertContains(page, "admin-autocomplete")
        self.assertNotContains(page, "Should not render")

    def test_revoking_and_closing_expired_access_is_audited(self):
        self.client.post(
            "/admin/app_portal/librarygrant/",
            {"action": "revoke", "_selected_action": self.grant.pk}, **administrator(),
        )
        self.assertTrue(LogEntry.objects.filter(
            object_id=str(self.grant.pk), change_message__contains="library_access.revoked",
        ).exists())
        expired = LibraryGrant.objects.create(
            profile=self.profile, library=self.engineering, reason="Old",
            granted_by=self.grant.granted_by, expires=timezone.now() - timedelta(days=1),
        )
        response = self.client.post(
            "/admin/app_portal/librarygrant/add/",
            {
                "profile": self.profile.pk, "library": ENGINEERING, "reason": "New",
                "_save": "Save",
            },
            **administrator(),
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(LogEntry.objects.filter(
            object_id=str(expired.pk), change_message__contains="library_access.revoked",
        ).exists())

    def test_simultaneous_submission_keeps_one_grant_without_an_error_page(self):
        with patch.object(LibraryGrantForm, "clean", lambda form: form.cleaned_data):
            response = self.client.post(
                "/admin/app_portal/librarygrant/add/",
                {
                    "profile": self.profile.pk, "library": ENGINEERING, "reason": "Twice",
                    "_save": "Save",
                },
                **administrator(),
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LibraryGrant.objects.filter(profile=self.profile).count(), 1)
