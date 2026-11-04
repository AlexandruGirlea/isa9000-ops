from __future__ import annotations

import re
import secrets
import time
import uuid
from contextlib import nullcontext

import jwt
import requests
from app_portal.models import EntraGroup, Profile
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import AnonymousUser
from django.middleware.csrf import get_token
from django.urls import Resolver404, resolve
from django.utils import timezone
from opentelemetry import trace

from core import sessions
from core.views import error_page
from shared import telemetry


def excluded(path):
    return (
        path in {
            "/", "/index.html", "/healthz", "/.auth/login/aad", "/.auth/logout",
            "/auth/login/aad/callback",
        }
        or path.startswith("/static/")
        or re.fullmatch(r"/internal/runs/[0-9a-fA-F-]{36}/[a-z]+", path) is not None
    )


class ErrorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            response.status_code >= 400
            and response.get("Content-Type", "").startswith("text/html")
            and not getattr(response, "portal_error", False)
        ):
            error = error_page(request, response.status_code)
            for header in ("Allow", "Retry-After", "WWW-Authenticate"):
                if header in response:
                    error[header] = response[header]
            return error
        return response


class RequestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(24)
        active = trace.get_current_span().get_span_context().is_valid
        context = (
            nullcontext()
            if active
            else telemetry.attach({"traceparent": request.headers.get("traceparent", "")})
        )
        with context:
            with trace.get_tracer(__name__).start_as_current_span("portal.request"):
                request.correlation_id = telemetry.correlation_id()
                try:
                    match = resolve(request.path)
                except Resolver404:
                    match = None
                route = match.route if match else "unmatched"
                monitored = request.path != "/healthz" and not request.path.startswith("/static/")
                action = "admin.access" if request.path.startswith("/admin/") else "http.request"
                response = self.get_response(request)
                response["X-Correlation-Id"] = request.correlation_id
                response.setdefault(
                    "Content-Security-Policy",
                    "default-src 'self'; "
                    f"script-src 'nonce-{request.csp_nonce}' "
                    f"{request.scheme}://{request.get_host()}/static/; script-src-attr 'none'; "
                    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                    "connect-src 'self'; media-src 'self'; font-src 'self'; "
                    "object-src 'none'; frame-src 'none'; base-uri 'none'; "
                    "form-action 'self'; frame-ancestors 'none'",
                )
                response.setdefault(
                    "Permissions-Policy", "camera=(), microphone=(), geolocation=()",
                )
                if monitored or (
                    hasattr(request, "user") and request.user.is_authenticated
                ):
                    response["Cache-Control"] = "private, no-store"
                if monitored and getattr(request, "audit_actor_id", ""):
                    authenticated = bool(getattr(request, "audit_actor_id", ""))
                    outcome = (
                        "failed" if response.status_code >= 500 else
                        "denied" if response.status_code >= 400 or (
                            not excluded(request.path) and not authenticated
                        ) else "success"
                    )
                    telemetry.audit(
                        action, outcome=outcome, route=route, method=request.method,
                        status_code=response.status_code,
                        actor_id=getattr(request, "audit_actor_id", ""),
                        **{
                            key: str(match.kwargs[key])
                            for key in ("course_id", "run_id", "group_id")
                            if match and key in match.kwargs
                        },
                    )
                return response


class IdentityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.user = AnonymousUser()
        if excluded(request.path):
            return self.get_response(request)
        if sessions.expired(request.session):
            return sessions.signin_required(request, expired=True)
        try:
            if settings.AUTH_MODE in {"dev", "entra"}:
                if settings.AUTH_MODE == "entra":
                    from core.entra import identity
                else:
                    from core.dev import identity

                person = identity(request)
                if not person:
                    return sessions.signin_required(request)
            else:
                from core.entra import verified_identity

                token = request.headers.get("X-MS-TOKEN-AAD-ID-TOKEN")
                if not token:
                    return sessions.signin_required(request)
                person = verified_identity(token)
            oid = str(uuid.UUID(person["oid"]))
            groups = {uuid.UUID(value) for value in person["groups"]}
        except jwt.ExpiredSignatureError:
            return sessions.signin_required(request, expired=True)
        except (ValueError, TypeError, KeyError, AttributeError, jwt.PyJWTError,
                requests.RequestException) as error:
            if str(error) == "Your sign-in has no library groups.":
                return error_page(
                    request, 403, "Your sign-in has no library groups. "
                    "Ask your administrator to assign groups to this app.",
                )
            return error_page(request, 403, "Your sign-in could not be verified.")
        request.audit_actor_id = oid
        trace.get_current_span().set_attribute("enduser.id", oid)
        superuser = (
            not person.get("local_test") and bool(settings.ADMIN_GROUP_ID)
            and settings.ADMIN_GROUP_ID in {str(group) for group in groups}
        )
        attributes = {
            "first_name": person["name"][:150],
            "email": person["email"][:254],
            "is_superuser": superuser,
            "is_staff": superuser,
        }
        user, _ = get_user_model().objects.get_or_create(
            username=oid,
            defaults={**attributes, "password": make_password(None)},
        )
        if not user.is_active:
            request.session.flush()
            return error_page(
                request, 403, "Your account is inactive. Contact your administrator."
            )
        changed = []
        for key, value in attributes.items():
            if getattr(user, key) != value:
                setattr(user, key, value)
                changed.append(key)
        if user.has_usable_password():
            user.set_unusable_password()
            changed.append("password")
        if changed:
            user.save(update_fields=changed)
        if request.session.get("session_oid") != oid:
            if settings.AUTH_MODE == "easyauth":
                request.session.flush()
            sessions.start(request.session, oid, request=request)
        profile, _ = Profile.objects.get_or_create(user=user)
        if set(profile.groups.values_list("pk", flat=True)) != groups:
            EntraGroup.objects.bulk_create(
                [EntraGroup(object_id=group) for group in groups], ignore_conflicts=True,
            )
            profile.groups.set(groups)
        now = timezone.now()
        if (now - profile.last_seen).total_seconds() >= 300:
            profile.last_seen = now
            profile.save(update_fields=["last_seen"])
        request.user = user
        if request.path.startswith("/admin/") and not superuser:
            return error_page(request, 404, "This page could not be found.")
        response = self.get_response(request)
        if response.status_code < 400 and (
            request.headers.get("Accept") != "application/json" or request.method != "GET"
        ):
            now = time.time()
            if now - request.session["last_activity"] >= 60:
                request.session["last_activity"] = now
                request.session.set_expiry(max(
                    1, int(request.session["signed_in_at"] + settings.SESSION_MAX_AGE - now),
                ))
        if (
            response.status_code == 200 and not response.streaming
            and response.get("Content-Type", "").startswith("text/html")
            and b"</body>" in response.content
        ):
            get_token(request)
            response.content = response.content.replace(
                b"</body>", b'<script src="/static/session.js" defer></script></body>', 1,
            )
            response.headers.pop("Content-Length", None)
        return response
