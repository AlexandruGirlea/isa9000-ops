from __future__ import annotations

import base64
import json
import re
from io import StringIO
from unittest.mock import patch

from app_portal.models import EntraGroup, LocalTestUser, Profile
from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, override_settings

from .helpers import (
    ADMIN,
    ENGINEERING,
    OID,
    SALES,
    LibraryCase,
    PortalCase,
    administrator,
    principal,
    token,
)


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
        unknown = EntraGroup.objects.get(pk=ADMIN)
        self.assertEqual((unknown.name, unknown.status), (ADMIN, "hidden"))

    def test_group_sync_removes_stale_membership_and_superuser(self):
        self.client.get(
            "/libraries/", HTTP_X_MS_TOKEN_AAD_ID_TOKEN=principal(groups=[SALES, ADMIN]),
        )
        self.client.get("/libraries/", HTTP_X_MS_TOKEN_AAD_ID_TOKEN=principal(groups=[ENGINEERING]))
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_superuser)
        self.assertFalse(self.user.is_staff)
        self.assertEqual(list(self.user.profile.groups.all()), [self.engineering])

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

    def test_anonymous_and_stale_session_redirect_to_signin(self):
        self.client.force_login(self.user)
        response = self.client.get("/libraries/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/.auth/login/aad"))

    def test_root_redirects_cookie_without_trusting_identity_header(self):
        self.client.cookies["AppServiceAuthSession"] = "present"
        response = self.client.get("/", **self.headers)
        self.assertEqual(response.url, "/libraries/")

    def test_local_sign_in_routes_are_absent_on_azure(self):
        for path in ("/.auth/login/aad", "/auth/login/aad/callback"):
            self.assertEqual(self.client.get(path).status_code, 404)

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


class SignOutTests(LibraryCase):
    def test_signing_out_ends_only_the_portal_session(self):
        page = self.client.get("/libraries/", **self.headers)
        self.assertContains(page, '<form method="post" action="/signout/">')
        self.assertNotContains(page, "/.auth/logout")
        for path, headers in (("/signout/", self.headers), ("/admin/logout/", administrator())):
            with self.subTest(path):
                self.client.get("/libraries/", **headers)
                self.assertIn("session_oid", self.client.session)
                self.assertEqual(self.client.get(path, **headers).status_code, 405)
                self.assertEqual(self.client.post(path, **headers).url, "/signed-out/")
                self.assertNotIn("session_oid", self.client.session)
        self.assertEqual(self.client.post("/admin/logout/", **self.headers).status_code, 404)

    def test_sign_out_form_passes_csrf(self):
        client = Client(enforce_csrf_checks=True)
        page = client.get("/libraries/", **self.headers).content.decode()
        form = re.search(r'action="/signout/">.*?value="([^"]+)"', page, re.S)
        self.assertEqual(client.post("/signout/", **self.headers).status_code, 403)
        response = client.post("/signout/", {"csrfmiddlewaretoken": form[1]}, **self.headers)
        self.assertEqual(response.url, "/signed-out/")

    def test_signed_out_page_clears_azure_without_following_it_to_microsoft(self):
        page = Client().get("/signed-out/")
        self.assertContains(page, 'fetch("/.auth/logout", {redirect: "manual"})')
        self.assertContains(page, 'location.replace("/")')
        self.assertNotContains(page, "/static/session.js")
        policy = page["Content-Security-Policy"]
        nonce = re.search(r'nonce="([^"]+)"', page.content.decode())[1]
        self.assertIn(f"'nonce-{nonce}'", policy)
        self.assertIn("connect-src 'self'", policy)
        self.assertEqual(Client().post("/signed-out/").status_code, 405)

    def test_an_ended_session_never_reaches_microsoft(self):
        self.client.get("/libraries/", **self.headers)
        self.assertEqual(self.client.get("/libraries/", **token(exp=1)).url, "/signed-out/")
        for method, path in (("get", "/libraries/"), ("post", "/session/activity/")):
            with self.subTest(path):
                self.client.get("/libraries/", **self.headers)
                session = self.client.session
                session["last_activity"] = 0
                session.save()
                response = getattr(self.client, method)(path, **self.headers)
                if method == "post":
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json(), {"signin": "/signed-out/"})
                else:
                    self.assertEqual(response.url, "/signed-out/")


@override_settings(AUTH_MODE="entra", LOCAL_TEST_USERS=True, ALLOWED_HOSTS=["localhost", "web"])
class LocalSignInTests(PortalCase):
    def setUp(self):
        super().setUp()
        self.local = Client(HTTP_HOST="localhost")

    def fake_user(self):
        call_command("fake_user", group=SALES, stdout=StringIO())
        entry = LocalTestUser.objects.latest("number")
        self.local.cookies["sessionid"] = entry.session_key
        return entry

    def real_session(self, **values):
        session = SessionStore()
        session.update({"entra_id_token": "test-real-token", **values})
        session.create()
        self.local.cookies["sessionid"] = session.session_key

    def test_a_real_sign_in_outranks_a_fake_session_and_ends_when_invalid(self):
        self.real_session(local_test_user=1)
        with (
            patch("core.entra.verified_identity", return_value={
                "oid": OID, "name": "Real person", "email": "", "groups": [SALES],
            }) as verified,
            patch("core.local_users.identity") as fake,
        ):
            response = self.local.get("/libraries/")
            self.assertEqual(response.wsgi_request.user.pk, self.user.pk)
            verified.assert_called_once_with("test-real-token")
            verified.side_effect = ValueError("Invalid token")
            self.assertRedirects(self.local.get("/libraries/"), "/")
            fake.assert_not_called()

    def test_signing_out_ends_the_session_on_the_landing_page(self):
        person = {"oid": OID, "name": "Real person", "email": "", "groups": [ADMIN]}
        with patch("core.entra.verified_identity", return_value=person):
            for path in ("/signout/", "/admin/logout/"):
                with self.subTest(path):
                    self.real_session()
                    self.assertEqual(self.local.get("/libraries/").status_code, 200)
                    key = self.local.cookies["sessionid"].value
                    self.assertEqual(self.local.post(path).url, "/")
                    self.assertFalse(Session.objects.filter(session_key=key).exists())
        self.assertEqual(self.local.get("/signed-out/").status_code, 404)

    def test_fake_user_is_a_limited_viewer_even_with_a_forged_token(self):
        user = self.fake_user().user
        self.assertEqual(user.profile.role, "viewer")
        self.assertFalse(user.has_usable_password() or user.is_staff or user.is_superuser)
        self.assertEqual(self.local.get(f"/libraries/{SALES}/").status_code, 200)
        response = self.local.get(
            "/libraries/", HTTP_X_MS_TOKEN_AAD_ID_TOKEN=principal(groups=[ADMIN]),
        )
        self.assertEqual(response.wsgi_request.user.pk, user.pk)
        self.assertFalse(response.wsgi_request.user.is_superuser)
        for path in (
            f"/libraries/{ENGINEERING}/", f"/libraries/{SALES}/new/",
            f"/courses/{self.course.id}/manage/", "/runs/", "/admin/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.local.get(path).status_code, 404)
        with patch("app_portal.runs.dispatch") as dispatch:
            self.assertEqual(self.local.post(
                f"/libraries/{SALES}/new/", {"title": "Test", "request": "Test"},
            ).status_code, 404)
        dispatch.assert_not_called()

    def test_fake_sessions_refuse_privileged_accounts_and_forged_cookies(self):
        entry = self.fake_user()
        user = entry.user
        for field, denied in (("is_active", False), ("is_staff", True), ("is_superuser", True)):
            with self.subTest(field=field):
                original = getattr(user, field)
                setattr(user, field, denied)
                user.save(update_fields=[field])
                self.local.cookies["sessionid"] = entry.session_key
                self.assertRedirects(self.local.get("/libraries/"), "/")
                with self.assertRaises(CommandError):
                    call_command("fake_user", number=entry.number, stdout=StringIO())
                setattr(user, field, original)
                user.save(update_fields=[field])
        admin = EntraGroup.objects.create(object_id=ADMIN, name="Admins", status="enabled")
        for options in ({"group": admin.pk}, {}):
            with self.subTest(options=options), self.assertRaises(CommandError):
                call_command("fake_user", stdout=StringIO(), **options)
        forged = SessionStore()
        forged["local_test_user"] = entry.number
        forged.create()
        self.local.cookies["sessionid"] = forged.session_key
        self.assertRedirects(self.local.get("/libraries/"), "/")

    def test_fake_sessions_stop_outside_the_local_stack(self):
        entry = self.fake_user()
        for options in (
            {"LOCAL_TEST_USERS": False},
            {"AUTH_MODE": "easyauth"},
            {"ALLOWED_HOSTS": ["localhost", "example.invalid"]},
        ):
            with self.subTest(disabled=options), override_settings(**options):
                with self.assertRaises(CommandError):
                    call_command("fake_user", group=SALES, stdout=StringIO())
                self.local.cookies["sessionid"] = entry.session_key
                response = self.local.get("/libraries/")
                self.assertEqual(response.status_code, 302)
                self.assertFalse(response.wsgi_request.user.is_authenticated)
