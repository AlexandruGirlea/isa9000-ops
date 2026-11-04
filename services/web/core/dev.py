from __future__ import annotations

import json
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods, require_POST

from core import sessions
from shared import telemetry

SALES = "11111111-1111-4111-8111-111111111111"
ENGINEERING = "22222222-2222-4222-8222-222222222222"
PEOPLE = {
    "viewer": {
        "oid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "name": "Demo Viewer",
        "email": "viewer@example.invalid",
        "groups": [SALES],
        "role": "viewer",
    },
    "creator": {
        "oid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "name": "Demo Creator",
        "email": "creator@example.invalid",
        "groups": [SALES],
        "role": "creator",
    },
    "admin": {
        "oid": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "name": "Demo Admin",
        "email": "admin@example.invalid",
        "groups": [SALES, ENGINEERING],
        "role": "creator",
    },
}


def identity(request):
    if not request.session.get("dev_authenticated"):
        return None
    if oid := request.session.get("dev_user"):
        user = get_user_model().objects.filter(username=oid, is_active=True).first()
        if not user:
            return None
        return {
            "oid": user.username,
            "name": user.get_full_name(),
            "email": user.email,
            "groups": [str(group.pk) for group in user.profile.groups.all()],
        }
    person = PEOPLE.get(request.session.get("dev_person"))
    if not person:
        return None
    value = os.environ.get("ISA9000_DEV_USER")
    if value:
        return {
            **json.loads(value),
            "groups": [
                group for group in os.environ.get("ISA9000_DEV_GROUPS", "").split(",") if group
            ],
        }
    person = {**person, "groups": list(person["groups"])}
    if request.session["dev_person"] == "admin" and settings.ADMIN_GROUP_ID:
        person["groups"].append(settings.ADMIN_GROUP_ID)
    return person


@require_http_methods(["GET", "POST"])
def login(request):
    if settings.AUTH_MODE != "dev" or not settings.DEBUG:
        raise Http404
    if request.method == "GET":
        return redirect("signin")
    request.session.cycle_key()
    request.session.pop("dev_user", None)
    request.session["dev_person"] = "admin"
    request.session["dev_authenticated"] = True
    request.audit_actor_id = identity(request)["oid"]
    sessions.start(request.session, request.audit_actor_id, request=request)
    telemetry.audit("auth.login", actor_id=request.audit_actor_id, target_type="local_demo")
    return redirect("libraries")


@require_POST
def logout(request):
    if settings.AUTH_MODE != "dev" or not settings.DEBUG:
        raise Http404
    person = identity(request)
    request.audit_actor_id = person["oid"] if person else ""
    request.session.flush()
    telemetry.audit("auth.logout", actor_id=request.audit_actor_id, target_type="local_demo")
    return redirect("signin")
