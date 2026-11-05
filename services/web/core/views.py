from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_safe
from isa9000.config import load_config

from core.branding import site
from core.uploads import UploadTooLarge


def page(request, template, context=None, status=200):
    return render(
        request,
        template,
        {
            "site": site(),
            "base_path": "/static/isa9000/",
            "nav_path": "/libraries/",
            "dev": settings.AUTH_MODE == "dev",
            "local_auth": settings.AUTH_MODE in {"dev", "entra"},
            **(context or {}),
        },
        status=status,
        using="jinja2",
    )


def error_page(request, status, message=None):
    title = {
        400: "Invalid request",
        403: "Access denied",
        404: "Page not found",
        405: "Method not allowed",
        409: "This action could not be completed",
        500: "Something went wrong",
        503: "Service unavailable",
    }.get(status, "Request could not be completed")
    response = HttpResponse(
        render_to_string(
            "error.html",
            {
                "status": status,
                "title": title,
                "message": message or (
                    "Please try again later."
                    if status >= 500 else "This request could not be completed."
                ),
                "reference": getattr(request, "correlation_id", ""),
            },
            using="django",
        ),
        status=status,
    )
    response.portal_error = True
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Frame-Options"] = "DENY"
    response["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    )
    return response


def not_found(request, exception=None):
    return error_page(request, 404, "This page could not be found.")


def server_error(request):
    return error_page(
        request, 500, "Something went wrong. Your administrators have been notified.",
    )


def bad_request(request, exception=None):
    message = str(exception) if isinstance(exception, UploadTooLarge) else (
        "This request could not be processed."
    )
    return error_page(request, 400, message)


def csrf_failure(request, reason="", exception=None):
    return error_page(request, 403, "Please reload the page and try again.")


def permission_denied(request, exception=None):
    return error_page(request, 403, "You do not have access to this action.")


@require_safe
def signin(request):
    if settings.AUTH_MODE == "entra":
        from core.entra import identity

        if identity(request):
            return redirect("libraries")
    if (
        (settings.AUTH_MODE == "easyauth" and request.COOKIES.get("AppServiceAuthSession"))
        or (
            settings.AUTH_MODE == "dev" and request.session.get("dev_authenticated")
            and (request.session.get("dev_person") or request.session.get("dev_user"))
        )
    ):
        response = redirect("libraries")
        response["Cache-Control"] = "private, no-store"
        return response
    return page(request, "signin.html", {"nav_path": "/"})


@require_safe
def health(request):
    return HttpResponse("ok", content_type="text/plain")


@require_safe
def logo(request):
    config = load_config()
    if not config.branding.logo or Path(config.logo_file).suffix.lower() != ".svg":
        raise Http404
    return FileResponse(config.logo_file.open("rb"), content_type="image/svg+xml")


@require_safe
def manifest(request):
    branding = site()
    return JsonResponse(
        {
            "name": branding["title"],
            "short_name": branding["title"],
            "start_url": "/libraries/",
            "display": "standalone",
            "background_color": branding["color"],
            "theme_color": branding["color"],
            "icons": [
                {
                    "src": "/static/isa9000/" + branding["logo"],
                    "sizes": "any",
                    "type": branding["logo_type"],
                }
            ],
        },
        content_type="application/manifest+json",
    )
