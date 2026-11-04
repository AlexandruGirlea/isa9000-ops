from __future__ import annotations

from app_portal import callbacks, descriptions, media, transcripts, views
from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from core import entra, sessions
from core import views as core_views

urlpatterns = [
    path("", core_views.signin, name="signin"),
    path("index.html", RedirectView.as_view(pattern_name="signin")),
    path("healthz", core_views.health),
    path(".auth/login/aad", entra.login),
    path(".auth/logout", entra.logout),
    path("auth/login/aad/callback", entra.callback),
    path("session/activity/", sessions.activity),
    path("libraries/", views.libraries, name="libraries"),
    path("libraries/new/", views.new_library, name="new_library"),
    path("libraries/index.html", RedirectView.as_view(pattern_name="libraries")),
    path("libraries/<uuid:group_id>/", views.library, name="library"),
    path("libraries/<uuid:group_id>/index.html", views.library_index),
    path("libraries/<uuid:group_id>/new/", views.create, name="create"),
    path("libraries/<uuid:group_id>/check/", views.check_source, name="check_source"),
    path("libraries/<uuid:group_id>/description-preview/", descriptions.preview),
    path("courses/<uuid:course_id>/", views.course_page, name="course"),
    path("courses/<uuid:course_id>/manage/", views.manage, name="manage"),
    path("courses/<uuid:course_id>/description-preview/", descriptions.preview),
    path(
        "courses/<uuid:course_id>/episodes/<int:episode_number>/transcript/",
        transcripts.transcript,
        name="transcript",
    ),
    path("runs/<uuid:run_id>/", views.run_page, name="run"),
    path("runs/", views.history, name="history"),
    path("media/<uuid:course_id>/<path:filename>", media.media),
    path("internal/runs/<uuid:run_id>/<str:action>", callbacks.callback),
    path("static/isa9000/logo.svg", core_views.logo),
    path("static/isa9000/manifest.webmanifest", core_views.manifest),
    path("admin/password_change/", core_views.not_found),
    path("admin/password_change/done/", core_views.not_found),
    path("admin/", admin.site.urls),
]
handler400 = "core.views.bad_request"
handler403 = "core.views.permission_denied"
handler404 = "core.views.not_found"
handler500 = "core.views.server_error"
