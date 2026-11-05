from __future__ import annotations

import re
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

import app_portal as portal
import jwt
from app_portal.library_admin import LibraryGrantForm
from app_portal.models import Course, EntraGroup, LibraryGrant, Profile, Run
from app_portal.permissions import groups_for
from core.audit import record
from core.dev import ENGINEERING, SALES
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, connection, transaction
from django.test import Client, override_settings
from django.utils import timezone

from .helpers import (
    ADMIN,
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

    def test_shared_library_page_hides_podcasts_of_a_hidden_owner(self):
        self.course.shared_groups.add(self.empty)
        Course.objects.create(group=self.empty, created_by=self.user, status="ready", title="Own")
        self.sales.status = "discovered"
        self.sales.save()
        page = self.client.get(f"/libraries/{OTHER}/", **viewer([OTHER]))
        self.assertContains(page, "Own")
        self.assertNotContains(page, "Sales course")

    def test_editor_icons_load_before_the_editor_so_the_csp_stays_strict(self):
        page = self.client.get(f"/libraries/{SALES}/new/", **self.headers).content.decode()
        icons = page.index('src="/static/vendor/vditor/dist/js/icons/ant.js" id="vditorIconScript"')
        self.assertLess(icons, page.index('src="/static/vendor/vditor/dist/index.min.js"'))

    def test_linked_pages_option_sits_with_the_urls_and_says_what_it_does(self):
        page = self.client.get(f"/libraries/{SALES}/new/", **self.headers).content.decode()
        option = page.index("Also read pages linked from these URLs")
        self.assertLess(page.index('id="source-editor"'), option)
        self.assertLess(option, page.index('class="upload-area"'))
        self.assertIn("Does not apply to uploaded files", page)

    def test_title_is_optional_when_creating_a_podcast(self):
        upload = SimpleUploadedFile("handbook.md", b"Handbook")
        with (
            patch("app_portal.runs.dispatch"),
            patch("app_portal.views.storage.save_upload"),
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                f"/libraries/{SALES}/new/",
                {"request": "Teach onboarding", "uploads": upload}, **self.headers,
            )
        self.assertEqual(response.status_code, 302)
        course = Run.objects.exclude(pk=self.run.pk).get().course
        self.assertEqual(course.title, "")
        self.assertContains(self.client.get("/runs/", **self.headers), "Podcast in progress")

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

    def test_administrators_get_the_details_in_the_admin(self):
        path = self.failed_run()
        page = self.client.get(path, **administrator())
        self.assertNotContains(page, "Traceback")
        self.assertContains(page, f"/admin/app_portal/run/{self.run.id}/change/")
        detail = self.client.get(f"/admin/app_portal/run/{self.run.id}/change/", **administrator())
        self.assertContains(detail, "secret-host.internal key=abc")

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


class BrowserPopupTests(LibraryCase):
    def test_portal_code_never_opens_a_browser_dialog(self):
        root = Path(portal.__file__).parent
        files = [*(root / "jinja2").glob("*.html"), root / "static" / "session.js"]
        native = re.compile(r"(?:(?<![\w.])|window\.)(?:alert|confirm|prompt)\(")
        self.assertGreater(len(files), 5)
        for file in files:
            with self.subTest(file=file.name):
                self.assertIsNone(native.search(file.read_text()))

    def test_admin_forms_have_no_popup_window_links(self):
        for path in (
            f"/admin/app_portal/course/{self.course.pk}/change/",
            "/admin/app_portal/librarygrant/add/",
        ):
            with self.subTest(path=path):
                page = self.client.get(path, **administrator())
                self.assertContains(page, "admin-autocomplete")
                self.assertNotContains(page, "add-related")


class SignOutTests(LibraryCase):
    def test_every_sign_out_ends_on_the_landing_page(self):
        target = "/.auth/logout?post_logout_redirect_uri=/"
        self.assertContains(self.client.get("/libraries/", **self.headers), f'href="{target}"')
        # A redirect here would be blocked by form-action on its way to Microsoft.
        page = self.client.post("/admin/logout/", **administrator())
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, f'http-equiv="refresh" content="0;url={target}"')
        self.assertIn("form-action 'self'", page["Content-Security-Policy"])
        self.assertEqual(self.client.get("/admin/logout/", **administrator()).status_code, 405)
        self.assertEqual(self.client.post("/admin/logout/", **self.headers).status_code, 404)
        with override_settings(AUTH_MODE="dev", DEBUG=True):
            client = Client()
            client.post("/.auth/login/aad")
            self.assertEqual(client.post("/admin/logout/").url, "/")
            self.assertEqual(client.get("/libraries/").url, "/")


class AdminCatalogueTests(LibraryCase):
    def setUp(self):
        super().setUp()
        make = EntraGroup.objects.create
        self.discovered_idle = make(object_id=uuid.uuid4(), name="Idle")
        self.unnamed_idle = make(object_id=uuid.uuid4())
        self.unnamed_second = make(object_id=uuid.uuid4())
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
        self.client.get("/libraries/", **administrator())
        admin_group = uuid.UUID(ADMIN)
        everything = set(EntraGroup.objects.values_list("pk", flat=True))
        hidden = everything - {self.sales.pk, self.engineering.pk, self.empty.pk}
        no_content = {
            self.discovered_idle.pk, self.unnamed_idle.pk, self.unnamed_second.pk,
            self.draft_share.pk, self.empty.pk, self.engineering.pk, admin_group,
        }
        for params, expected in (
            ({}, everything),
            ({"status": "enabled"}, {self.sales.pk, self.engineering.pk, self.empty.pk}),
            ({"status": "discovered"}, hidden),
            ({"status": "review"}, {self.unnamed_idle.pk, self.unnamed_second.pk, admin_group}),
            ({"named": "missing"}, {
                self.unnamed_idle.pk, self.unnamed_second.pk, self.unnamed_shared.pk,
                admin_group,
            }),
            ({"show_when_empty__exact": 1}, {self.mixed_owner.pk}),
            ({"content": "none"}, no_content),
            ({"content": "owned"}, {
                self.sales.pk, self.draft_owner.pk, self.mixed_owner.pk,
            }),
            ({"content": "shared"}, {self.unnamed_shared.pk}),
            ({"content": "drafts"}, {self.draft_owner.pk}),
        ):
            with self.subTest(params=params):
                self.assertEqual(self.rows(**params), expected)

    def test_admin_renames_and_enables_a_group_on_its_page(self):
        response = self.client.post(
            f"/admin/app_portal/entragroup/{self.unnamed_idle.pk}/change/",
            {"name": "Finance", "status": "enabled", "show_when_empty": "on", "_save": "Save"},
            **administrator(),
        )
        self.assertEqual(response.status_code, 302)
        self.unnamed_idle.refresh_from_db()
        self.assertEqual(self.unnamed_idle.name, "Finance")
        self.assertEqual(self.unnamed_idle.status, "enabled")
        self.assertTrue(self.unnamed_idle.show_when_empty)
        self.assertTrue(LogEntry.objects.filter(
            object_id=str(self.unnamed_idle.pk), change_message__contains="admin.change",
        ).exists())
        page = self.client.get("/admin/app_portal/entragroup/", **administrator())
        self.assertNotContains(page, 'name="form-0-name"')

    def test_status_help_explains_every_choice(self):
        page = self.client.get(
            f"/admin/app_portal/entragroup/{self.unnamed_idle.pk}/change/", **administrator(),
        )
        self.assertContains(page, "<b>Hidden:</b> nobody can use it in the portal")
        self.assertContains(page, "<b>Enabled:</b> a library")
        self.assertNotContains(page, "Archived")

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
        self.assertContains(page, ">2 people</a>")
        LibraryGrant.objects.create(
            profile=current, library=self.engineering, granted_by=self.user, reason="Both",
        )
        page = self.client.get(
            f"/admin/app_portal/entragroup/{ENGINEERING}/change/", **administrator(),
        )
        self.assertContains(page, ">2 people</a>")
        LibraryGrant.objects.all().delete()
        page = self.client.get(
            f"/admin/app_portal/entragroup/{ENGINEERING}/change/", **administrator(),
        )
        self.assertContains(page, ">1 person</a>")

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

    def test_only_enabled_libraries_can_be_granted(self):
        library = EntraGroup.objects.create(
            object_id=uuid.uuid4(), name="Closed", status="discovered",
        )
        response = self.client.post(
            "/admin/app_portal/librarygrant/add/",
            {"profile": self.profile.pk, "library": str(library.pk), "_save": "Save"},
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
