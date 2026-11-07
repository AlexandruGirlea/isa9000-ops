from __future__ import annotations

import uuid
from datetime import timedelta
from io import StringIO

from app_portal.models import Course, EntraGroup, LibraryGrant, Profile
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from .helpers import (
    ADMIN,
    ADMIN_OID,
    ENGINEERING,
    OVERAGE,
    SALES,
    TENANT,
    LibraryCase,
    administrator,
    token,
)


class LocalAccessTests(LibraryCase):
    def setUp(self):
        super().setUp()
        self.grantor = get_user_model().objects.create_user(username=ADMIN_OID)
        Profile.objects.create(user=self.grantor)

    def grant(self, library=None, profile=None, **fields):
        return LibraryGrant.objects.create(
            profile=profile or self.profile, library=library or self.engineering,
            granted_by=self.grantor, reason="Pilot", **fields,
        )

    def test_grant_gives_access_and_survives_every_kind_of_sign_in(self):
        self.engineering_course = Course.objects.create(
            group=self.engineering, created_by=self.user, status="ready", title="Engineering",
            html="<!doctype html>Engineering",
        )
        self.grant()
        for headers in (self.headers, token(groups=[]), token(**OVERAGE), token(), self.headers):
            self.assertEqual(self.status(f"/libraries/{ENGINEERING}/", headers), 200)
            self.assertEqual(self.status(f"/courses/{self.engineering_course.id}/", headers), 200)
        self.assertEqual(LibraryGrant.objects.active().count(), 1)

    def test_unresolved_sign_in_never_uses_the_stale_snapshot(self):
        self.assertEqual(self.status(f"/courses/{self.course.id}/", self.headers), 200)
        for claims in (OVERAGE, {"hasgroups": True}, {"groups": "broken"}):
            with self.subTest(claims=sorted(claims)):
                headers = token(**claims)
                self.assertEqual(self.status(f"/courses/{self.course.id}/", headers), 404)
                self.assertEqual(
                    self.status(f"/media/{self.course.id}/01 - First.mp3", headers), 404,
                )
                self.assertEqual(self.status(f"/libraries/{SALES}/", headers), 404)
                self.assertEqual(self.status(f"/libraries/{SALES}/new/", headers), 404)
                self.assertEqual(self.status(f"/courses/{self.course.id}/manage/", headers), 404)
                self.assertFalse(self.listed(headers))
                self.assertEqual(list(self.profile.groups.all()), [self.sales])
        self.assertEqual(self.status(f"/courses/{self.course.id}/", self.headers), 200)

    def test_local_grant_does_not_promote_a_viewer_or_grant_editing_rights(self):
        Profile.objects.filter(pk=self.profile.pk).update(role="viewer")
        self.grant(self.sales)
        overage = token(**OVERAGE)
        self.assertEqual(self.status(f"/courses/{self.course.id}/", overage), 200)
        self.assertEqual(self.status(f"/courses/{self.course.id}/manage/", overage), 404)
        self.assertEqual(self.status(f"/libraries/{SALES}/new/", overage), 404)
        self.assertEqual(self.status("/admin/", overage), 404)

    def test_revocation_and_expiry_apply_on_the_next_request(self):
        grant = self.grant(self.sales)
        overage = token(**OVERAGE)
        self.assertEqual(self.status(f"/courses/{self.course.id}/", overage), 200)
        grant.revoked, grant.revoked_by = timezone.now(), self.grantor
        grant.save()
        self.assertEqual(self.status(f"/courses/{self.course.id}/", overage), 404)
        again = self.grant(self.sales, expires=timezone.now() + timedelta(hours=1))
        self.assertEqual(self.status(f"/courses/{self.course.id}/", overage), 200)
        LibraryGrant.objects.filter(pk=again.pk).update(expires=timezone.now())
        self.assertEqual(self.status(f"/courses/{self.course.id}/", overage), 404)

    def test_hidden_library_ignores_local_grants(self):
        self.grant(self.sales)
        self.sales.status = "hidden"
        self.sales.save()
        self.assertEqual(self.status(f"/courses/{self.course.id}/", token(**OVERAGE)), 404)

    def test_suspended_users_stay_blocked_with_grants_or_admin_rights(self):
        self.grant(self.sales)
        Profile.objects.filter(pk=self.profile.pk).update(local_admin=True)
        self.user.is_active = False
        self.user.save()
        for headers in (self.headers, token(**OVERAGE), token(groups=[ADMIN])):
            self.assertEqual(self.status("/libraries/", headers), 403)
            self.assertEqual(self.status(f"/courses/{self.course.id}/", headers), 403)
            self.assertEqual(self.status("/admin/", headers), 403)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

class AdministratorTests(LibraryCase):
    def edit(self, profile, headers, **fields):
        data = {"role": profile.role, "is_active": "on", "_save": "Save", **fields}
        return self.client.post(
            f"/admin/app_portal/profile/{profile.pk}/change/", data, **headers,
        )

    def test_library_membership_never_implies_administration(self):
        library = EntraGroup.objects.create(object_id=ADMIN, name="Admins", status="enabled")
        self.profile.groups.add(library)
        LibraryGrant.objects.create(
            profile=self.profile, library=library, granted_by=self.user, reason="Test",
        )
        for headers in (self.headers, token(**OVERAGE), token(groups=[])):
            self.assertEqual(self.status("/admin/", headers), 404)
            self.user.refresh_from_db()
            self.assertFalse(self.user.is_superuser or self.user.is_staff)

    def test_directory_admin_with_overage_needs_the_local_flag(self):
        overage = token(ADMIN_OID, **OVERAGE)
        self.assertEqual(self.status("/admin/", administrator()), 200)
        self.assertEqual(self.status("/admin/", overage), 404)
        Profile.objects.filter(user__username=ADMIN_OID).update(local_admin=True)
        self.assertEqual(self.status("/admin/", overage), 200)
        self.assertTrue(get_user_model().objects.get(username=ADMIN_OID).is_superuser)

    def test_admins_grant_others_but_never_themselves(self):
        self.client.get("/libraries/", **administrator())
        me = Profile.objects.get(user__username=ADMIN_OID)
        response = self.edit(me, administrator(), local_admin="on")
        self.assertEqual(response.status_code, 302)
        me.refresh_from_db()
        self.assertFalse(me.local_admin)
        response = self.edit(me, administrator(), is_active="")
        me.user.refresh_from_db()
        self.assertTrue(me.user.is_active)
        response = self.edit(self.profile, administrator(), local_admin="on")
        self.assertEqual(response.status_code, 302)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.local_admin)
        self.assertEqual(self.status("/admin/", self.headers), 200)
        self.assertTrue(LogEntry.objects.filter(
            change_message__contains="admin.app_admin_granted",
        ).exists())
        self.assertEqual(self.edit(self.profile, administrator()).status_code, 302)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.local_admin)
        self.assertTrue(LogEntry.objects.filter(
            change_message__contains="admin.app_admin_revoked",
        ).exists())

    def test_regular_users_cannot_reach_grant_or_admin_endpoints(self):
        for path in (
            "/admin/app_portal/librarygrant/", "/admin/app_portal/librarygrant/add/",
            "/admin/app_portal/entragroup/add/",
            f"/admin/app_portal/profile/{self.profile.pk}/change/",
        ):
            self.assertEqual(self.status(path, self.headers), 404)
            self.assertEqual(self.status(path, self.headers, "post", local_admin="on"), 404)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.local_admin)

    def test_recovery_command_restores_a_locked_out_administrator(self):
        oid = str(uuid.uuid4())
        arguments = ("--tenant", TENANT, "--object-id", oid)
        with self.assertRaisesMessage(CommandError, "does not match"):
            call_command(
                "app_admin", "grant", "--tenant", str(uuid.uuid4()), "--object-id", oid,
                stdout=StringIO(),
            )
        self.assertFalse(get_user_model().objects.filter(username=oid).exists())
        call_command("app_admin", "grant", *arguments, stdout=StringIO())
        overage = token(oid, **OVERAGE)
        self.assertEqual(self.status("/admin/", overage), 200)
        self.assertTrue(LogEntry.objects.filter(change_message__contains="operator_cli").exists())
        activity = self.client.get("/admin/admin/logentry/", **overage)
        self.assertContains(activity, "Operator command")
        get_user_model().objects.filter(username=oid).update(is_active=False)
        output = StringIO()
        call_command("app_admin", "grant", *arguments, stdout=output)
        self.assertIn("stays blocked", output.getvalue())
        self.assertEqual(self.status("/admin/", overage), 403)
        call_command("app_admin", "grant", *arguments, "--activate", stdout=StringIO())
        self.assertEqual(self.status("/admin/", overage), 200)
        call_command("app_admin", "revoke", *arguments, stdout=StringIO())
        self.assertEqual(self.status("/admin/", overage), 404)

    def test_admin_adds_a_library_and_a_blank_name_becomes_the_group_id(self):
        new = str(uuid.uuid4())
        form = {"object_id": new, "name": "  ", "status": "enabled", "_save": "Save"}
        response = self.client.post("/admin/app_portal/entragroup/add/", form, **administrator())
        self.assertEqual(response.status_code, 302)
        library = EntraGroup.objects.get(pk=new)
        self.assertEqual((library.name, library.status), (new, "enabled"))
        self.assertTrue(LogEntry.objects.filter(change_message__contains="admin.add").exists())
        form["name"] = "Copy"
        self.assertContains(
            self.client.post("/admin/app_portal/entragroup/add/", form, **administrator()),
            "already exists",
        )
