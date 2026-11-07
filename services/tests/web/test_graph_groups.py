from __future__ import annotations

import json
import time
from unittest.mock import patch

import requests
from app_portal.models import Profile
from django.test import Client, override_settings

from .helpers import ADMIN, ADMIN_OID, ENGINEERING, OID, OTHER, OVERAGE, SALES, LibraryCase, token

ACCESS = {"HTTP_X_MS_TOKEN_AAD_ACCESS_TOKEN": "graph-token"}
OVERFLOW = {**token(**OVERAGE), **ACCESS}
ME = "https://graph.microsoft.com/v1.0/me"


def reply(status=200, body=None, raw=None):
    response = requests.Response()
    response.status_code = status
    response._content = raw if raw is not None else json.dumps(body).encode()
    return response


def graph(oid=OID, groups=(SALES,)):
    def answer(method, url, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Bearer graph-token"
        assert kwargs["allow_redirects"] is False
        assert kwargs["timeout"] == (2, 3)
        if url == f"{ME}?$select=id" and method == "get":
            return reply(body={"id": oid})
        if url == f"{ME}/getMemberObjects" and method == "post":
            assert kwargs["json"] == {"securityEnabledOnly": True}
            return reply(body={"value": list(groups)})
        raise AssertionError(f"Unexpected call: {method} {url}")

    return answer


class GraphOffTests(LibraryCase):
    def test_off_by_default_makes_no_call_even_with_a_token(self):
        with patch("requests.sessions.Session.request") as request:
            response = self.client.get("/libraries/", **OVERFLOW)
        self.assertContains(response, "local library access")
        request.assert_not_called()


@override_settings(GRAPH_GROUPS=True)
class GraphTests(LibraryCase):
    def test_overflow_gets_libraries_with_two_calls_once(self):
        for claims in (OVERAGE, {"hasgroups": True}):
            with self.subTest(claims=sorted(claims)), patch(
                "requests.sessions.Session.request", side_effect=graph(),
            ) as request:
                self.client = Client()
                headers = {**token(**claims), **ACCESS}
                response = self.client.get("/libraries/", **headers)
                self.assertNotContains(response, "local library access")
                self.assertEqual(self.status(f"/libraries/{SALES}/", headers), 200)
                self.assertEqual(self.status(f"/courses/{self.course.id}/", headers), 200)
                self.assertEqual(self.status(f"/libraries/{ENGINEERING}/", headers), 404)
                self.assertTrue(self.listed(headers))
                self.assertEqual(request.call_count, 2)
                self.profile.refresh_from_db()
                self.assertEqual(self.profile.membership, "complete")
                self.assertEqual(list(self.profile.groups.all()), [self.sales])

    def test_someone_elses_access_token_grants_nothing(self):
        with patch(
            "requests.sessions.Session.request", side_effect=graph(oid=OTHER),
        ) as request:
            response = self.client.get("/libraries/", **OVERFLOW)
            self.assertContains(response, "local library access")
            self.assertEqual(self.status(f"/libraries/{SALES}/", OVERFLOW), 404)
            self.assertEqual(request.call_count, 1)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.membership, "overage")

    def test_admin_group_from_graph_gives_admin_only_for_its_owner(self):
        headers = {**token(ADMIN_OID, **OVERAGE), **ACCESS}
        with patch(
            "requests.sessions.Session.request",
            side_effect=graph(oid=OTHER, groups=[ADMIN]),
        ):
            self.assertEqual(self.client.get("/admin/", **headers).status_code, 404)
        self.client = Client()
        with patch(
            "requests.sessions.Session.request",
            side_effect=graph(oid=ADMIN_OID, groups=[ADMIN]),
        ):
            self.assertEqual(self.client.get("/admin/", **headers).status_code, 200)

    def test_failures_fail_closed_and_retry_after_a_minute(self):
        failures = {
            "timeout": requests.ConnectTimeout(),
            "forbidden": reply(403, {"error": {"code": "Authorization_RequestDenied"}}),
            "throttled": reply(429, {"error": {"code": "TooManyRequests"}}),
            "redirect": reply(302, raw=b""),
            "not json": reply(raw=b"<html>"),
            "no id": reply(body={}),
            "bad id": reply(body={"id": 123}),
            "list body": reply(body=[]),
        }
        for name, failure in failures.items():
            with self.subTest(name), patch(
                "requests.sessions.Session.request", side_effect=[failure],
            ) as request:
                self.client = Client()
                response = self.client.get("/libraries/", **OVERFLOW)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "local library access")
                self.assertEqual(self.status(f"/libraries/{SALES}/", OVERFLOW), 404)
                self.assertEqual(request.call_count, 1)
            now = time.time()
            with patch(
                "requests.sessions.Session.request", side_effect=graph(),
            ) as request, patch("core.entra.time.time", return_value=now + 61):
                self.assertEqual(self.status(f"/libraries/{SALES}/", OVERFLOW), 200)
                self.assertEqual(request.call_count, 2)

    def test_bad_group_lists_fail_closed(self):
        for name, groups in {"not uuids": ["x"], "numbers": [1], "not a list": "x"}.items():
            with self.subTest(name), patch(
                "requests.sessions.Session.request",
                side_effect=[reply(body={"id": OID}), reply(body={"value": groups})],
            ):
                self.client = Client()
                self.assertContains(
                    self.client.get("/libraries/", **OVERFLOW), "local library access",
                )

    def test_cached_list_is_not_reused_for_someone_else(self):
        with patch("requests.sessions.Session.request", side_effect=graph()):
            self.assertEqual(self.status(f"/libraries/{SALES}/", OVERFLOW), 200)
        other = {**token(OTHER, **OVERAGE), **ACCESS}
        with patch(
            "requests.sessions.Session.request", side_effect=graph(oid=OTHER, groups=[]),
        ) as request:
            self.assertEqual(self.status(f"/libraries/{SALES}/", other), 404)
            self.assertEqual(request.call_count, 2)
        self.assertEqual(self.client.session["graph_groups"]["oid"], OTHER)

    def test_access_token_is_never_stored_and_failures_are_reported(self):
        with patch(
            "requests.sessions.Session.request", side_effect=[reply(403, {})],
        ), patch("core.entra.telemetry.audit") as audit:
            self.client.get("/libraries/", **OVERFLOW)
        audit.assert_any_call(
            "auth.graph_groups", outcome="failed", actor_id=OID, state="http_403",
        )
        with patch("requests.sessions.Session.request", side_effect=graph()):
            self.client = Client()
            self.client.get("/libraries/", **OVERFLOW)
        self.assertNotIn("graph-token", json.dumps(dict(self.client.session)))
        self.assertTrue(Profile.objects.filter(pk=self.profile.pk, membership="complete").exists())
