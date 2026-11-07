from __future__ import annotations

import time

from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse
from django.middleware.csrf import rotate_token
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_POST

SIGNED_OUT = "/signed-out/"

def start(session, oid, *, request=None):
    now = time.time()
    session.update({"session_oid": oid, "signed_in_at": now, "last_activity": now})
    session.set_expiry(settings.SESSION_MAX_AGE)
    if request is not None:
        rotate_token(request)


def expired(session):
    started = session.get("signed_in_at")
    if started is None:
        return False
    now = time.time()
    return (
        now < started or now - started >= settings.SESSION_MAX_AGE
        or now - session.get("last_activity", started) >= settings.SESSION_IDLE_TIMEOUT
    )


def signin_required(request, *, expired=False):
    target = (
        SIGNED_OUT
        if expired and settings.AUTH_MODE == "easyauth" else
        settings.LOGIN_URL if settings.AUTH_MODE == "easyauth" else "/"
    )
    request.session.flush()
    if request.path == "/session/activity/":
        return JsonResponse({"signin": target}, status=401)
    return redirect(target)


@require_GET
def signed_out(request):
    if settings.AUTH_MODE != "easyauth":
        raise Http404
    # Easy Auth clears its cookie and tokens on this call. Following its redirect would also sign
    # the person out of Microsoft, and so out of everything else they have open there.
    return HttpResponse(
        f'<!doctype html><title>Signing out</title><script nonce="{request.csp_nonce}">'
        'fetch("/.auth/logout", {redirect: "manual"}).finally(() => location.replace("/"))'
        "</script>"
    )


@require_POST
def activity(request):
    return JsonResponse({"ok": True})
