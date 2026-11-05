from __future__ import annotations

import time
import uuid
from functools import lru_cache
from urllib.parse import urlsplit

import jwt
import msal
import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import Http404, HttpResponse
from django.middleware.csrf import rotate_token
from django.shortcuts import redirect
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core import dev, sessions
from core.views import error_page
from shared import telemetry


@lru_cache(maxsize=4)
def signing_keys(tenant):
    return jwt.PyJWKClient(
        f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys", timeout=10
    )


def directory_groups(claims):
    # Entra omits the list above its limit, so an overage token never means "only these groups".
    # The token's _claim_sources URLs are deliberately never followed.
    names = claims.get("_claim_names")
    if claims.get("hasgroups") is True or (isinstance(names, dict) and "groups" in names):
        return [], "overage"
    if "groups" not in claims:
        return [], "missing"
    try:
        if not isinstance(claims["groups"], list):
            raise ValueError
        return [str(uuid.UUID(group)) for group in claims["groups"]], "complete"
    except (ValueError, TypeError, AttributeError):
        return [], "failed"


def verified_identity(token):
    tenant = str(uuid.UUID(settings.ENTRA_TENANT_ID))
    client = str(uuid.UUID(settings.ENTRA_CLIENT_ID))
    if not isinstance(token, str) or len(token) > 30000:
        raise ValueError("Invalid token.")
    key = signing_keys(tenant).get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token, key.key, algorithms=["RS256"], audience=client,
        issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
        options={"require": ["exp", "iat", "iss", "aud", "oid", "tid"]},
    )
    if claims["tid"] != tenant:
        raise ValueError("Invalid token.")
    groups, membership = directory_groups(claims)
    return {
        "oid": str(uuid.UUID(claims["oid"])),
        "name": claims.get("name", ""),
        "email": claims.get("preferred_username", ""),
        "groups": groups,
        "membership": membership,
        "expires": claims["exp"],
    }


GRAPH_ME = "https://graph.microsoft.com/v1.0/me"


def graph_groups(request, person):
    # /me answers for whoever owns the access token, which the ID token's signature does not
    # cover, so the owner is compared before the list can grant anything.
    now = time.time()
    cached = request.session.get("graph_groups")
    if cached and cached["oid"] == person["oid"] and now < cached["until"]:
        return cached["groups"]
    token = request.headers.get("X-MS-TOKEN-AAD-ACCESS-TOKEN")
    if not token:
        return None
    call = {
        "headers": {"Authorization": f"Bearer {token}"},
        "timeout": (2, 3),
        "allow_redirects": False,
    }
    groups, until = None, now + 60
    try:
        owner = requests.get(f"{GRAPH_ME}?$select=id", **call)
        owner.raise_for_status()
        if str(uuid.UUID(owner.json()["id"])) != person["oid"]:
            raise ValueError("Access token belongs to someone else.")
        answer = requests.post(
            f"{GRAPH_ME}/getMemberObjects", json={"securityEnabledOnly": True}, **call
        )
        answer.raise_for_status()
        listed, state = directory_groups({"groups": answer.json()["value"]})
        if state != "complete":
            raise ValueError("Unreadable group list.")
        groups, until = listed, person["expires"]
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError) as error:
        response = getattr(error, "response", None)
        reason = type(error).__name__ if response is None else f"http_{response.status_code}"
        telemetry.audit("auth.graph_groups", outcome="failed", actor_id=person["oid"], state=reason)
    request.session["graph_groups"] = {"oid": person["oid"], "until": until, "groups": groups}
    return groups


def identity(request):
    if sessions.expired(request.session):
        request.session.flush()
        return None
    token = request.session.get("entra_id_token")
    if not token:
        if "local_test_user" in request.session:
            from core.local_users import identity as local_identity

            return local_identity(request)
        return None
    try:
        try:
            return verified_identity(token)
        except jwt.ExpiredSignatureError:
            cache = msal.SerializableTokenCache()
            cache.deserialize(request.session.get("entra_token_cache", ""))
            app = client(cache)
            accounts = app.get_accounts()
            if len(accounts) != 1:
                raise ValueError("Sign in again.") from None
            result = app.acquire_token_silent([], account=accounts[0], force_refresh=True)
            token = (result or {}).get("id_token")
            person = verified_identity(token)
            if person["oid"] != request.session.get("session_oid"):
                raise ValueError("Sign-in identity changed.") from None
            request.session["entra_id_token"] = token
            request.session["entra_token_cache"] = cache.serialize()
            return person
    except (ValueError, TypeError, KeyError, jwt.PyJWTError, requests.RequestException):
        request.session.flush()
        return None


def client(cache=None):
    tenant = str(uuid.UUID(settings.ENTRA_TENANT_ID))
    client_id = str(uuid.UUID(settings.ENTRA_CLIENT_ID))
    uri = urlsplit(settings.ENTRA_REDIRECT_URI)
    if (
        not settings.ENTRA_CLIENT_SECRET
        or uri.scheme != "http"
        or uri.hostname not in {"localhost", "127.0.0.1"}
        or uri.path != "/auth/login/aad/callback"
        or uri.username or uri.password or uri.query or uri.fragment
    ):
        raise ValueError("Microsoft sign-in is not configured.")
    return msal.ConfidentialClientApplication(
        client_id, client_credential=settings.ENTRA_CLIENT_SECRET,
        authority=f"https://login.microsoftonline.com/{tenant}",
        token_cache=cache, timeout=10, enable_pii_log=False,
    )


@require_http_methods(["GET", "POST"])
def login(request):
    if settings.AUTH_MODE == "dev":
        return dev.login(request)
    if settings.AUTH_MODE != "entra":
        raise Http404
    try:
        flow = client().initiate_auth_code_flow(
            scopes=[], redirect_uri=settings.ENTRA_REDIRECT_URI, prompt="select_account",
        )
    except (ValueError, requests.RequestException):
        telemetry.audit("auth.login", outcome="failed", state="configuration_unavailable")
        return error_page(
            request, 503, "Microsoft sign-in is not available yet. Contact your administrator."
        )
    request.session.flush()
    request.session["entra_flow"] = flow
    request.session["entra_flow_started"] = time.time()
    request.session.set_expiry(600)
    telemetry.audit("auth.login", outcome="started", target_type="entra")
    return redirect(flow["auth_uri"])


@require_GET
def callback(request):
    if settings.AUTH_MODE != "entra":
        raise Http404
    flow = request.session.pop("entra_flow", None)
    started = request.session.pop("entra_flow_started", 0)
    try:
        if not flow or not 0 <= time.time() - started <= 600:
            raise ValueError("Sign-in expired.")
        cache = msal.SerializableTokenCache()
        result = client(cache).acquire_token_by_auth_code_flow(flow, request.GET.dict())
        token = result.get("id_token")
        person = verified_identity(token)
    except (
        ValueError, RuntimeError, TypeError, KeyError, jwt.PyJWTError, requests.RequestException,
    ):
        request.session.flush()
        telemetry.audit("auth.login", outcome="denied", target_type="entra")
        return error_page(
            request, 403, "Microsoft sign-in could not be verified. Please sign in again."
        )
    request.session.flush()
    if get_user_model().objects.filter(username=person["oid"], is_active=False).exists():
        telemetry.audit("auth.login", outcome="denied", actor_id=person["oid"], state="inactive")
        return error_page(request, 403, "Your account is inactive. Contact your administrator.")
    request.session["entra_id_token"] = token
    request.session["entra_token_cache"] = cache.serialize()
    sessions.start(request.session, person["oid"], request=request)
    request.audit_actor_id = person["oid"]
    telemetry.audit("auth.login", actor_id=person["oid"], target_type="entra")
    return redirect("libraries")


@require_POST
def logout(request):
    if settings.AUTH_MODE == "dev":
        return dev.logout(request)
    if settings.AUTH_MODE != "entra":
        raise Http404
    person = identity(request)
    request.audit_actor_id = person["oid"] if person else ""
    request.session.flush()
    rotate_token(request)
    telemetry.audit(
        "auth.logout", actor_id=request.audit_actor_id,
        target_type="local_test" if person and person.get("local_test") else "entra",
    )
    return redirect("signin")


@require_POST
def admin_logout(request):
    if settings.AUTH_MODE != "easyauth":
        return logout(request)
    request.session.flush()
    telemetry.audit("auth.logout", actor_id=request.audit_actor_id, target_type="entra")
    # The form-action policy blocks a form's redirects that reach Microsoft; a new navigation works.
    return HttpResponse(
        '<!doctype html><title>Signing out</title><meta http-equiv="refresh" '
        f'content="0;url={sessions.EASY_AUTH_LOGOUT}">'
    )
