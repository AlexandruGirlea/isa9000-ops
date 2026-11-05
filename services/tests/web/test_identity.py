from __future__ import annotations

import base64
import json
import os
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from app_portal.models import Course, EntraGroup, LocalTestUser, Profile, Run
from core.dev import ENGINEERING, SALES
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings
from django.utils import timezone
from opentelemetry import trace

from .helpers import ADMIN, OID, PortalCase, principal


class IdentityTests(PortalCase):
    def test_first_visit_registers_viewer_and_lists_unknown_groups_as_hidden(self):
        oid = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        response = self.client.get(
            "/libraries/", HTTP_X_MS_TOKEN_AAD_ID_TOKEN=principal(oid, [SALES, ADMIN])
        )
        self.assertEqual(response.status_code, 200)
        user = get_user_model().objects.get(username=oid)
        self.assertEqual(user.profile.role, "viewer")
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.profile.groups.count(), 2)
        unknown = EntraGroup.objects.get(pk=ADMIN)
        self.assertEqual((unknown.name, unknown.status), (ADMIN, "discovered"))
        with (
            override_settings(
                AUTH_MODE="entra", LOCAL_TEST_USERS=True, ALLOWED_HOSTS=["localhost"],
            ),
            patch("core.entra.verified_identity", return_value={
                "oid": OID, "name": "Real person", "email": "", "groups": [SALES],
            }) as verified,
            patch("core.local_users.identity") as fake,
        ):
            session = SessionStore()
            session.update({"entra_id_token": "test-real-token", "local_test_user": 1})
            session.create()
            client = Client(HTTP_HOST="localhost")
            client.cookies["sessionid"] = session.session_key
            response = client.get("/libraries/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.wsgi_request.user.pk, self.user.pk)
            verified.assert_called_once_with("test-real-token")
            fake.assert_not_called()
            verified.side_effect = ValueError("Invalid token")
            self.assertRedirects(client.get("/libraries/"), "/")
            self.assertFalse(Session.objects.filter(session_key=session.session_key).exists())
            fake.assert_not_called()

    def test_group_sync_removes_stale_membership_and_superuser(self):
        self.client.get(
            "/libraries/", HTTP_X_MS_TOKEN_AAD_ID_TOKEN=principal(groups=[SALES, ADMIN]),
        )
        self.client.get("/libraries/", HTTP_X_MS_TOKEN_AAD_ID_TOKEN=principal(groups=[ENGINEERING]))
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_superuser)
        self.assertFalse(self.user.is_staff)
        self.assertEqual(list(self.user.profile.groups.all()), [self.engineering])
        self.assertEqual(self.user.profile.role, "creator")

    def test_excluded_paths_ignore_forged_headers(self):
        oid = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
        for path in (
            "/",
            "/healthz",
            "/static/isa9000/manifest.webmanifest",
            f"/internal/runs/{self.run.id}/accept",
        ):
            with self.subTest(path=path):
                self.client.get(path, HTTP_X_MS_TOKEN_AAD_ID_TOKEN=principal(oid, [ADMIN]))
                self.assertFalse(get_user_model().objects.filter(username=oid).exists())

    def test_invalid_headers_are_refused(self):
        headers = [
            "bad-base64",
            base64.b64encode(b"[]").decode(),
            principal("not-an-oid", [SALES]),
            base64.b64encode(json.dumps({"auth_typ": "other", "claims": []}).encode()).decode(),
        ]
        for header in headers:
            with self.subTest(header=header):
                response = self.client.get("/libraries/", HTTP_X_MS_TOKEN_AAD_ID_TOKEN=header)
                self.assertEqual(response.status_code, 403)
                self.assertContains(response, "Reference:", status_code=403)

    def test_missing_group_claim_gives_actionable_message(self):
        response = self.client.get("/libraries/", HTTP_X_MS_TOKEN_AAD_ID_TOKEN=principal())
        self.assertContains(response, "local library access")
        self.assertContains(response, OID)

    def test_anonymous_and_stale_session_redirect_to_signin(self):
        self.client.force_login(self.user)
        response = self.client.get("/libraries/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/.auth/login/aad"))

    def test_root_redirects_cookie_without_trusting_identity_header(self):
        self.client.cookies["AppServiceAuthSession"] = "present"
        response = self.client.get("/", **self.headers)
        self.assertEqual(response.url, "/libraries/")

    @override_settings(AUTH_MODE="dev", DEBUG=True)
    def test_dev_signin_and_logout(self):
        for configured_user in ("", json.dumps({"oid": OID, "name": "Demo", "email": ""})):
            with (
                self.subTest(configured_identity=bool(configured_user)),
                patch.dict(os.environ, {
                    "ISA9000_DEV_USER": configured_user,
                    "ISA9000_DEV_GROUPS": f"{SALES},{ADMIN}",
                }),
            ):
                client = Client()
                self.assertRedirects(client.get("/libraries/"), "/")
                self.assertRedirects(client.get("/.auth/login/aad"), "/")
                response = client.post("/.auth/login/aad")
                self.assertRedirects(response, "/libraries/")
                self.assertContains(client.get(f"/libraries/{SALES}/"), "New podcast")
                old_session = client.cookies["sessionid"].value
                self.assertRedirects(client.post("/.auth/logout"), "/")
                client.cookies["sessionid"] = old_session
                for path in (
                    "/libraries/", f"/libraries/{SALES}/", f"/libraries/{SALES}/new/",
                    f"/courses/{self.course.id}/", f"/courses/{self.course.id}/manage/",
                    f"/media/{self.course.id}/01 - First.mp3", "/runs/",
                    f"/runs/{self.run.id}/", "/admin/",
                ):
                    response = client.get(path, follow=True)
                    self.assertEqual(response.redirect_chain, [("/", 302)])
                    self.assertContains(response, "Enter local demo")
                    self.assertFalse(response.wsgi_request.user.is_authenticated)

        with (
            override_settings(
                AUTH_MODE="entra", LOCAL_TEST_USERS=True,
                ALLOWED_HOSTS=["localhost", "127.0.0.1", "web"],
            ),
            patch.dict(os.environ, {"ISA9000_FAKE_USER_NUMBER": ""}),
            patch("app_portal.runs.dispatch") as dispatch,
            patch("app_portal.views.storage.save_upload") as upload,
        ):
            output = StringIO()
            call_command("fake_user", stdout=output)
            entry = LocalTestUser.objects.get()
            user = entry.user
            self.assertIn(f"Fake user #{entry.number}", output.getvalue())
            self.assertIn("Viewer", output.getvalue())
            self.assertFalse(user.has_usable_password())
            self.assertFalse(user.is_staff or user.is_superuser)
            self.assertEqual(user.profile.role, "viewer")
            self.assertEqual(list(user.profile.groups.all()), [self.sales])
            session = Session.objects.get(session_key=entry.session_key)
            self.assertEqual(session.get_decoded()["local_test_user"], entry.number)
            self.assertLessEqual(session.expire_date, timezone.now() + timedelta(hours=8))
            self.assertGreater(session.expire_date, timezone.now() + timedelta(hours=7))
            audit = LogEntry.objects.filter(object_id=str(entry.number)).latest("id")
            self.assertIn("auth.local_test_session_issued", audit.change_message)
            self.assertNotIn(entry.session_key, audit.change_message)
            self.assertNotIn(user.email, audit.change_message)
            client = Client(HTTP_HOST="localhost")
            client.cookies["sessionid"] = entry.session_key
            self.assertEqual(client.get(f"/libraries/{SALES}/").status_code, 200)
            self.assertEqual(client.get(f"/courses/{self.course.id}/").status_code, 200)
            self.assertEqual(client.get("/libraries/", HTTP_HOST="127.0.0.1").status_code, 200)
            response = client.get(
                "/libraries/", HTTP_X_MS_TOKEN_AAD_ID_TOKEN=principal(groups=[ADMIN]),
            )
            self.assertEqual(response.wsgi_request.user.pk, user.pk)
            self.assertFalse(response.wsgi_request.user.is_superuser)
            for path in (
                f"/libraries/{ENGINEERING}/", f"/libraries/{SALES}/new/",
                f"/courses/{self.course.id}/manage/", "/runs/", "/admin/",
            ):
                with self.subTest(viewer_path=path):
                    self.assertEqual(client.get(path).status_code, 404)
            self.assertEqual(client.post(
                f"/libraries/{SALES}/new/", {"title": "Test", "request": "Test request"},
            ).status_code, 404)
            self.assertEqual(client.post(
                f"/courses/{self.course.id}/manage/", {"action": "record"},
            ).status_code, 404)
            dispatch.assert_not_called()
            upload.assert_not_called()
            self.assertEqual(Course.objects.count(), 1)
            self.assertEqual(Run.objects.count(), 1)

            call_command("fake_user", stdout=StringIO())
            self.assertEqual(LocalTestUser.objects.count(), 2)
            user.profile.role = "creator"
            user.profile.save(update_fields=["role"])
            user.profile.groups.set([self.engineering])
            old_key = entry.session_key
            call_command("fake_user", number=entry.number, stdout=StringIO())
            entry.refresh_from_db()
            self.assertNotEqual(old_key, entry.session_key)
            self.assertEqual(LocalTestUser.objects.count(), 2)
            self.assertRedirects(client.get("/libraries/"), "/")
            client.cookies["sessionid"] = entry.session_key
            self.assertEqual(client.get(f"/libraries/{ENGINEERING}/new/").status_code, 200)
            self.assertEqual(client.get(f"/libraries/{SALES}/").status_code, 404)
            user.profile.refresh_from_db()
            self.assertEqual(user.profile.role, "creator")
            self.assertEqual(list(user.profile.groups.all()), [self.engineering])

            counts = (
                get_user_model().objects.count(), LocalTestUser.objects.count(),
                Session.objects.count(),
            )
            missing = StringIO()
            call_command("fake_user", number=999999, stdout=missing)
            self.assertEqual(missing.getvalue(), "No user found\n")
            self.assertEqual(counts, (
                get_user_model().objects.count(), LocalTestUser.objects.count(),
                Session.objects.count(),
            ))
            with patch.dict(os.environ, {"ISA9000_FAKE_USER_NUMBER": str(entry.number)}):
                call_command("fake_user", stdout=StringIO())
            entry.refresh_from_db()
            self.assertEqual(LocalTestUser.objects.count(), 2)
            client.cookies["sessionid"] = entry.session_key
            Session.objects.filter(session_key=entry.session_key).update(
                expire_date=timezone.now() - timedelta(seconds=1),
            )
            self.assertRedirects(client.get("/libraries/"), "/")
            call_command("fake_user", number=entry.number, stdout=StringIO())
            entry.refresh_from_db()
            old_key = entry.session_key
            client.cookies["sessionid"] = old_key
            self.assertRedirects(client.post("/.auth/logout"), "/")
            self.assertFalse(Session.objects.filter(session_key=old_key).exists())
            client.cookies["sessionid"] = old_key
            self.assertRedirects(client.get("/libraries/"), "/")

            call_command("fake_user", number=entry.number, stdout=StringIO())
            entry.refresh_from_db()
            for field, denied in (("is_active", False), ("is_staff", True), ("is_superuser", True)):
                with self.subTest(disallowed_user_field=field):
                    original = getattr(user, field)
                    setattr(user, field, denied)
                    user.save(update_fields=[field])
                    client.cookies["sessionid"] = entry.session_key
                    self.assertRedirects(client.get("/libraries/"), "/")
                    with self.assertRaises(CommandError):
                        call_command("fake_user", number=entry.number, stdout=StringIO())
                    setattr(user, field, original)
                    user.save(update_fields=[field])
            admin, _ = EntraGroup.objects.get_or_create(
                object_id=ADMIN, defaults={"name": "Administrators"},
            )
            user.profile.groups.add(admin)
            client.cookies["sessionid"] = entry.session_key
            self.assertRedirects(client.get("/libraries/"), "/")
            with self.assertRaises(CommandError):
                call_command("fake_user", number=entry.number, stdout=StringIO())
            with self.assertRaises(CommandError):
                call_command("fake_user", group=admin.pk, stdout=StringIO())
            user.profile.groups.remove(admin)
            forged = SessionStore()
            forged["local_test_user"] = entry.number
            forged.create()
            client.cookies["sessionid"] = forged.session_key
            self.assertRedirects(client.get("/libraries/"), "/")
            dispatch.assert_not_called()

    def test_dev_endpoints_are_absent_in_easyauth(self):
        self.assertEqual(self.client.get("/.auth/login/aad").status_code, 404)
        self.assertEqual(self.client.post("/.auth/logout").status_code, 404)
        with (
            override_settings(
                AUTH_MODE="entra", LOCAL_TEST_USERS=True, ALLOWED_HOSTS=["localhost", "web"],
            ),
            patch.dict(os.environ, {"ISA9000_FAKE_USER_NUMBER": ""}),
        ):
            call_command("fake_user", stdout=StringIO())
            entry = LocalTestUser.objects.get()
            client = Client(HTTP_HOST="localhost")
            for options in (
                {"LOCAL_TEST_USERS": False},
                {"AUTH_MODE": "easyauth"},
                {"ALLOWED_HOSTS": ["localhost", "example.invalid"]},
            ):
                with self.subTest(disabled=options), override_settings(**options):
                    with self.assertRaises(CommandError):
                        call_command("fake_user", stdout=StringIO())
                    client.cookies["sessionid"] = entry.session_key
                    response = client.get("/libraries/")
                    self.assertEqual(response.status_code, 302)
                    self.assertFalse(response.wsgi_request.user.is_authenticated)
            for host in ("web", "example.invalid"):
                with self.subTest(host=host):
                    client.cookies["sessionid"] = entry.session_key
                    response = client.get("/libraries/", HTTP_HOST=host)
                    self.assertIn(response.status_code, (302, 400))
                    if response.status_code == 302:
                        self.assertFalse(response.wsgi_request.user.is_authenticated)
            with override_settings(ALLOWED_HOSTS=["localhost", "example.invalid"]):
                client.cookies["sessionid"] = entry.session_key
                response = client.get("/libraries/", HTTP_HOST="example.invalid")
                self.assertEqual(response.status_code, 302)
                self.assertFalse(response.wsgi_request.user.is_authenticated)
            self.assertEqual(LocalTestUser.objects.count(), 1)
            for value in (0, -1):
                with self.subTest(invalid_number=value), self.assertRaises(CommandError):
                    call_command("fake_user", number=value, stdout=StringIO())
            with patch.dict(os.environ, {"ISA9000_FAKE_USER_NUMBER": "invalid"}):
                with self.assertRaises(CommandError):
                    call_command("fake_user", stdout=StringIO())

    @override_settings(AUTH_MODE="dev", DEBUG=True)
    def test_dev_signin_post_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post("/.auth/login/aad", {"person": "admin"}).status_code, 403)
        with (
            override_settings(
                AUTH_MODE="entra", LOCAL_TEST_USERS=True, ALLOWED_HOSTS=["localhost"],
            ),
            patch.dict(os.environ, {"ISA9000_FAKE_USER_NUMBER": ""}),
        ):
            call_command("fake_user", stdout=StringIO())
            entry = LocalTestUser.objects.get()
            client.cookies["sessionid"] = entry.session_key
            response = client.post("/.auth/logout", HTTP_HOST="localhost")
            self.assertEqual(response.status_code, 403)
            self.assertTrue(Session.objects.filter(session_key=entry.session_key).exists())

    def test_existing_http_trace_is_kept(self):
        context = trace.SpanContext(
            trace_id=int("a" * 32, 16), span_id=12, is_remote=False, trace_flags=trace.TraceFlags(1)
        )
        with trace.use_span(trace.NonRecordingSpan(context)):
            response = self.client.get("/healthz")
        self.assertEqual(response["X-Correlation-Id"], "a" * 32)

    def test_signin_does_not_log_personal_information(self):
        with patch("core.middleware.telemetry.log") as log:
            self.client.get("/libraries/", **self.headers)
        self.assertNotIn("A Person", str(log.mock_calls))
        self.assertNotIn("a@example.invalid", str(log.mock_calls))
        events = [call.kwargs["extra"] for call in log.return_value.info.call_args_list]
        self.assertTrue(any(
            event["action"] == "http.request" and event["actor_id"] == OID for event in events
        ))
        self.assertEqual(Profile.objects.get(user__username=OID).role, "creator")
