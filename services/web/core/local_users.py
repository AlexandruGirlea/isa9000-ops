from __future__ import annotations

import hmac
from urllib.parse import urlsplit

from app_portal.models import LocalTestUser
from django.conf import settings


def enabled():
    return (
        settings.LOCAL_TEST_USERS and settings.AUTH_MODE == "entra"
        and set(settings.ALLOWED_HOSTS) <= {"localhost", "127.0.0.1", "web"}
    )


def identity(request):
    if not enabled() or urlsplit(f"//{request.get_host()}").hostname not in {
        "localhost", "127.0.0.1",
    }:
        return None
    number = request.session.get("local_test_user")
    if type(number) is not int or number <= 0:
        return None
    entry = LocalTestUser.objects.select_related("user__profile").filter(
        number=number, user__profile__isnull=False,
    ).first()
    if (
        not entry or not entry.user.is_active or entry.user.is_staff or entry.user.is_superuser
        or not entry.session_key
        or not hmac.compare_digest(entry.session_key, request.session.session_key or "")
    ):
        return None
    groups = [str(group.pk) for group in entry.user.profile.groups.all()]
    if settings.ADMIN_GROUP_ID and settings.ADMIN_GROUP_ID in groups:
        return None
    return {
        "oid": entry.user.username, "name": entry.user.get_full_name(),
        "email": entry.user.email, "groups": groups, "local_test": True,
    }
