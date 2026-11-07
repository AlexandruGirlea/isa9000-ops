from __future__ import annotations

import uuid
from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

import jwt
from app_portal.models import Course, EntraGroup, LibraryGrant, Profile
from app_portal.permissions import groups_for
from core.audit import record
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from .helpers import (
    ADMIN_OID,
    CLIENT,
    ENGINEERING,
    OTHER,
    SALES,
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

    def test_shared_library_page_hides_podcasts_of_a_hidden_owner(self):
        self.course.shared_groups.add(self.empty)
        Course.objects.create(group=self.empty, created_by=self.user, status="ready", title="Own")
        self.sales.status = "hidden"
        self.sales.save()
        page = self.client.get(f"/libraries/{OTHER}/", **viewer([OTHER]))
        self.assertContains(page, "Own")
        self.assertNotContains(page, "Sales course")

    def test_editor_icons_load_before_the_editor_so_the_csp_stays_strict(self):
        page = self.client.get(f"/libraries/{SALES}/new/", **self.headers).content.decode()
        icons = page.index('src="/static/vendor/vditor/dist/js/icons/ant.js" id="vditorIconScript"')
        self.assertLess(icons, page.index('src="/static/vendor/vditor/dist/index.min.js"'))

    def test_history_drops_podcasts_from_libraries_the_person_lost(self):
        self.assertContains(self.client.get("/runs/", **self.headers), "Sales course")
        self.assertNotContains(self.client.get("/runs/", **token(groups=[])), "Sales course")

    def test_a_request_is_judged_by_its_own_token_not_the_stored_snapshot(self):
        self.profile.groups.set([self.engineering])
        user = get_user_model().objects.get(pk=self.user.pk)
        user.directory_groups = {self.sales.pk}
        self.assertEqual(list(groups_for(user)), [self.sales])
        user.directory_groups = set()
        self.assertEqual(list(groups_for(user)), [])


class RunOutcomeTests(LibraryCase):
    def failed_run(self):
        self.run.status, self.run.error = "failed", "Traceback: secret-host.internal key=abc"
        self.run.log = ["Connecting to secret-host.internal"]
        self.run.save()
        return f"/runs/{self.run.id}/"

    def test_creators_get_a_friendly_failure_and_no_raw_error_or_log(self):
        path = self.failed_run()
        page = self.client.get(path, **self.headers)
        self.assertContains(page, "Something went wrong")
        self.assertContains(page, self.run.correlation_id)
        self.assertNotContains(page, "secret-host")
        data = self.client.get(path, HTTP_ACCEPT="application/json", **self.headers).json()
        self.assertNotIn("secret-host", str(data))
        self.assertEqual(data["log"], [])

    def test_cancel_button_warns_and_history_names_who_canceled(self):
        self.run.status = "queued"
        self.run.save()
        self.user.first_name = "Ana Creator"
        self.user.save()
        path = f"/runs/{self.run.id}/"
        page = self.client.get(path, **self.headers)
        self.assertContains(page, "Cancel processing</button>")
        self.assertContains(page, 'data-confirm="Cancel processing? This stops the run and deletes')
        self.assertNotContains(page, ">Stop<")
        response = self.client.post(path, {"action": "stop"}, **self.headers)
        self.assertEqual(response.status_code, 302)
        self.run.refresh_from_db()
        self.assertEqual((self.run.status, self.run.canceled_by), ("stopped", self.user))
        self.assertTrue(LogEntry.objects.filter(
            object_id=str(self.run.pk), change_message__contains="run.canceled",
        ).exists())
        self.assertContains(
            self.client.get(path, **self.headers), "by A Person (a@example.invalid)",
        )
        history = self.client.get("/runs/", **self.headers)
        self.assertContains(history, "User canceled")
        self.assertContains(
            history, '<span class="history-canceled-by">by A Person (a@example.invalid)</span>',
        )


class AuditTrailTests(LibraryCase):
    def test_admin_shows_who_created_and_last_edited_by_name_and_cannot_change_activity(self):
        self.client.get("/libraries/", **self.headers)
        self.user.refresh_from_db()
        record(self.user, "course.updated", self.course, changed_fields=["title"])
        page = self.client.get(
            f"/admin/app_portal/course/{self.course.pk}/change/", **administrator(),
        )
        self.assertContains(page, "Last edited by")
        self.assertContains(page, "A Person (a@example.invalid), ")
        self.assertNotContains(page, f"<div class=\"readonly\">{self.user.username}</div>")
        entry = LogEntry.objects.first()
        for path, method in (
            (f"/admin/admin/logentry/{entry.pk}/change/", "post"),
            (f"/admin/admin/logentry/{entry.pk}/delete/", "post"),
            ("/admin/admin/logentry/add/", "get"),
        ):
            response = getattr(self.client, method)(path, {"post": "yes"}, **administrator())
            self.assertEqual(response.status_code, 403, path)
        self.assertTrue(LogEntry.objects.filter(pk=entry.pk).exists())

    @skipUnless(connection.vendor == "postgresql", "The database trigger exists on PostgreSQL.")
    def test_database_refuses_changes_to_the_audit_log(self):
        record(self.user, "course.updated", self.course, changed_fields=["title"])
        entry = LogEntry.objects.first()
        for change in (
            lambda: LogEntry.objects.filter(pk=entry.pk).update(change_message="forged"),
            lambda: LogEntry.objects.filter(pk=entry.pk).delete(),
        ):
            with self.assertRaisesMessage(DatabaseError, "cannot be changed"), transaction.atomic():
                change()


class AdminCatalogueTests(LibraryCase):
    def test_admin_renames_and_enables_a_group_on_its_page(self):
        group = EntraGroup.objects.create(object_id=uuid.uuid4())
        response = self.client.post(
            f"/admin/app_portal/entragroup/{group.pk}/change/",
            {"name": "Finance", "status": "enabled", "_save": "Save"}, **administrator(),
        )
        self.assertEqual(response.status_code, 302)
        group.refresh_from_db()
        self.assertEqual((group.name, group.status), ("Finance", "enabled"))
        self.assertTrue(LogEntry.objects.filter(
            object_id=str(group.pk), change_message__contains="admin.change",
        ).exists())


class LocalAccessRecordTests(LibraryCase):
    def setUp(self):
        super().setUp()
        self.client.get("/libraries/", **administrator())
        self.grant = LibraryGrant.objects.create(
            profile=self.profile, library=self.engineering, reason="Test",
            granted_by=Profile.objects.get(user__username=ADMIN_OID).user,
        )

    def test_reason_and_expiry_are_optional_and_no_expiry_means_no_end(self):
        library = EntraGroup.objects.create(object_id=uuid.uuid4(), name="Open", status="enabled")
        response = self.client.post(
            "/admin/app_portal/librarygrant/add/",
            {"profile": self.profile.pk, "library": str(library.pk), "_save": "Save"},
            **administrator(),
        )
        self.assertEqual(response.status_code, 302)
        grant = LibraryGrant.objects.get(library=library)
        self.assertEqual(grant.reason, "")
        self.assertIsNone(grant.expires)
        self.assertTrue(LibraryGrant.objects.active().filter(pk=grant.pk).exists())
        with patch(
            "django.utils.timezone.now", return_value=timezone.now() + timedelta(days=3650),
        ):
            self.assertTrue(LibraryGrant.objects.active().filter(pk=grant.pk).exists())

    def test_records_cannot_be_edited_or_deleted(self):
        change = f"/admin/app_portal/librarygrant/{self.grant.pk}/change/"
        response = self.client.post(change, {"reason": "Edited"}, **administrator())
        self.assertEqual(response.status_code, 403)
        delete = f"/admin/app_portal/librarygrant/{self.grant.pk}/delete/"
        response = self.client.post(delete, {"post": "yes"}, **administrator())
        self.assertEqual(response.status_code, 403)
        self.grant.refresh_from_db()
        self.assertEqual(self.grant.reason, "Test")

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
