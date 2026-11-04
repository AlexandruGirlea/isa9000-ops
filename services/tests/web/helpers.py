from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from app_portal.models import Course, EntraGroup, Profile, Run
from core.dev import ENGINEERING, SALES
from cryptography.hazmat.primitives.asymmetric import rsa
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from shared import payloads

ADMIN = "33333333-3333-4333-8333-333333333333"
OID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TENANT = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
CLIENT = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def principal(oid=OID, groups=None, **extra):
    claims = {
        "oid": oid, "name": "A Person", "preferred_username": "a@example.invalid",
        "tid": TENANT, "aud": CLIENT, "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "iat": int(time.time()), "exp": int(time.time()) + 3600, **extra,
    }
    if groups is not None:
        claims["groups"] = groups
    return jwt.encode(claims, SIGNING_KEY, algorithm="RS256")


@override_settings(
    SECURE_SSL_REDIRECT=False,
    AUTH_MODE="easyauth",
    ADMIN_GROUP_ID=ADMIN,
    CALLBACK_SECRET="tests-only-callback",
    CALLBACK_BASE_URL="http://testserver",
    ENTRA_TENANT_ID=TENANT,
    ENTRA_CLIENT_ID=CLIENT,
    ALLOWED_HOSTS=["testserver"],
)
class PortalCase(TestCase):
    def setUp(self):
        keys = patch("core.entra.signing_keys", return_value=SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=SIGNING_KEY.public_key()),
        ))
        keys.start()
        self.addCleanup(keys.stop)
        self.sales = EntraGroup.objects.create(object_id=SALES, name="Sales")
        self.engineering = EntraGroup.objects.create(object_id=ENGINEERING, name="Engineering")
        self.sales.refresh_from_db()
        self.engineering.refresh_from_db()
        self.user = get_user_model().objects.create_user(username=OID)
        self.profile = Profile.objects.create(user=self.user, role="creator")
        self.profile.groups.add(self.sales)
        self.manifest = {
            "title": "Sales course",
            "description": "Learning",
            "cover": "cover.png",
            "episodes": [
                {
                    "number": 1,
                    "title": "First",
                    "audio": "01 - First.mp3",
                    "transcript": "01 - First.txt",
                    "seconds": 60,
                },
                {
                    "number": 2,
                    "title": "Second",
                    "audio": "02 - Second.mp3",
                    "transcript": "02 - Second.txt",
                    "seconds": 90,
                },
            ],
        }
        self.course = Course.objects.create(
            group=self.sales,
            created_by=self.user,
            title="Sales course",
            status="ready",
            cover="cover.png",
            manifest=self.manifest,
            html="<!doctype html>Course",
        )
        self.run = Run.objects.create(
            course=self.course,
            requested_by=self.user,
            task="create",
            correlation_id="a" * 32,
            request="Teach sales",
            sources=[],
        )
        self.headers = {"HTTP_X_MS_TOKEN_AAD_ID_TOKEN": principal(groups=[SALES])}

    def callback(self, action, body=None, run=None, **headers):
        run_id = (run or self.run).id
        response = self.client.post(
            f"/internal/runs/{run_id}/{action}",
            data=json.dumps(payloads.encrypt(body or {}, f"callback:{run_id}:{action}")),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer tests-only-callback",
            **headers,
        )
        if response.status_code == 200:
            body = payloads.decrypt(response.json(), f"reply:{run_id}:{action}")
            response.json = lambda: body
        return response
