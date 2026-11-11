from __future__ import annotations

import re
import uuid
from copy import deepcopy
from pathlib import PurePosixPath

from azure.core.exceptions import ResourceNotFoundError
from core.audit import record
from core.branding import player_html
from core.security import link_checks_exceeded, source_url
from core.views import error_page, page
from django.conf import settings
from django.contrib.admin.models import ADDITION, DELETION
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST, require_safe
from isa9000.links import MAX_SOURCES, MAX_URL_CHARS
from isa9000.sources import MAX_PDF_PAGES

from app_portal.covers import CoverForm, update_cover
from app_portal.forms import ApprovalForm, CreateForm, TitleForm
from app_portal.models import Course, Run
from app_portal.permissions import (
    can_manage_course,
    course_for,
    group_for,
    groups_for,
    is_creator,
)
from app_portal.runs import new_run
from shared import storage

CANCEL_WARNING = (
    "Cancel processing? This stops the run and deletes any transcripts and recordings it has "
    "created so far. It cannot be resumed."
)
HISTORY_LABELS = {
    "queued": "Queued", "running": "In progress", "ready": "Ready", "needs_review": "Needs review",
    "failed": "Failed", "stopped": "User canceled", "draft": "Draft",
}
FAILURE_MESSAGE = (
    "Something went wrong and this podcast could not be finished. "
    "Your administrators have been notified."
)
SOURCE_PROBLEMS = (
    "fix these sources first:", " is too large (", "no readable text was found",
    "the sources may not cover the request",
)


@require_safe
def course_page(request, course_id):
    course = course_for(request.user, course_id)
    if course.status != "ready" or not course.html:
        if can_manage_course(request.user, course):
            runs = course.runs.order_by("-created", "-id")
            active = runs.filter(status__in=["queued", "running"]).first()
            if active:
                return redirect("run", run_id=active.id)
            latest = runs.first()
            if (
                course.status != "needs_review" and latest
                and latest.status in {"failed", "stopped"}
            ):
                return redirect("run", run_id=latest.id)
            return redirect("manage", course_id=course.id)
        raise Http404
    # The worker's page is the same for everyone; the navbar depends on who is viewing.
    nav = render_to_string("portal_nav.html", request=request, using="jinja2")
    css = render_to_string("portal_nav.css", using="jinja2")
    html = re.sub(
        r'(<div class="ui-toggles">.*?</div>)', lambda match: match[1] + nav, course.html,
        count=1, flags=re.S,
    )
    accessible = groups_for(request.user).filter(
        Q(pk=course.group_id) | Q(shared_courses=course)
    ).distinct()
    try:
        selected = accessible.filter(pk=uuid.UUID(request.GET.get("library", ""))).first()
    except ValueError:
        selected = None
    selected = selected or accessible.filter(pk=course.group_id).first() or accessible.first()
    if selected and selected.pk != course.group_id:
        breadcrumb = render_to_string(
            "course_breadcrumbs.html", {"course": course, "group": selected}, using="jinja2",
        )
        html = re.sub(
            r'<nav class="course-breadcrumbs"[^>]*>.*?</nav>', lambda _: breadcrumb,
            html, count=1, flags=re.S,
        ).replace(f'href="/libraries/{course.group_id}/', f'href="/libraries/{selected.pk}/')
    if can_manage_course(request.user, course):
        action = render_to_string("course_edit.html", {"course": course}, using="jinja2")
        html = re.sub(r"(<h1>.*?</h1>)", lambda m: m[1] + action, html, count=1, flags=re.S)
    html = player_html(html, request, course)
    return HttpResponse(html.replace("</head>", f"<style>{css}</style></head>", 1))


@require_http_methods(["GET", "POST"])
def create(request, group_id):
    group = group_for(request.user, group_id, manage=True)
    form = CreateForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                course = Course.objects.create(
                    group=group, created_by=request.user,
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data["description"],
                    generation_settings=form.cleaned_data["generation_settings"],
                )
                record(
                    request.user, "course.created", course, flag=ADDITION,
                    course_id=str(course.id), group_id=str(group.pk),
                )
                run_id = uuid.uuid4()
                sources = list(form.cleaned_data["links"])
                uploads = form.cleaned_data["uploads"]
                names = [
                    f"uploads/{run_id}/{index}-{PurePosixPath(file.name).name}"
                    for index, file in enumerate(uploads, 1)
                ]
                run = new_run(
                    course,
                    request.user,
                    "create",
                    form.cleaned_data["request"],
                    [*sources, *names],
                    form.cleaned_data["follow_links"],
                    run_id,
                    text_only=form.cleaned_data["text_only"],
                )
                for upload, name in zip(uploads, names, strict=True):
                    storage.save_upload(upload, name)
            return redirect("run", run_id=run.id)
        except ValueError as error:
            form.add_error(None, str(error))
    return page(
        request,
        "create.html",
        {
            "group": group,
            "form": form,
            "max_files": settings.MAX_FILES,
            "max_upload_mb": settings.MAX_UPLOAD_MB,
            "max_sources": MAX_SOURCES,
            "max_url_chars": MAX_URL_CHARS,
            "max_pdf_pages": MAX_PDF_PAGES,
        },
    )


@require_POST
def check_source(request, group_id):
    group_for(request.user, group_id, manage=True)
    if link_checks_exceeded(request.user, 1):
        return JsonResponse({"ok": False, "detail": "Too many checks."}, status=429)
    try:
        source_url(request.POST.get("source", "").strip())
    except ValidationError as error:
        return JsonResponse({"ok": False, "detail": error.messages[0]})
    return JsonResponse({"ok": True, "detail": "Valid public URL"})


def history_row(course):
    run = next(iter(course.history_runs), None)
    status = run.status if run and run.status != "ready" else course.status
    if status == "needs_review":
        action, url = "Review transcripts", f"/courses/{course.id}/manage/"
    elif run:
        action = (
            "View progress" if status in {"queued", "running"}
            else "View result" if status == "ready" else "View details"
        )
        url = f"/runs/{run.id}/"
    elif status == "ready":
        action, url = "View podcast", f"/courses/{course.id}/"
    else:
        action, url = "Edit podcast", f"/courses/{course.id}/manage/"
    return {
        "id": str(course.id),
        "title": course.title or "Podcast in progress",
        "status": status,
        "label": HISTORY_LABELS.get(status, "Draft"),
        "canceled_by": run.canceled_by_name if run and status == "stopped" else "",
        "action": action,
        "url": url,
    }


@require_safe
def history(request):
    if not is_creator(request.user):
        raise Http404
    courses = (
        Course.objects.filter(created_by=request.user, group__in=groups_for(request.user))
        .select_related("group")
        .defer("html", "manifest", "generation_settings")
        .prefetch_related(
            Prefetch(
                "runs",
                queryset=Run.objects.select_related("canceled_by")
                .only(
                    "id", "course_id", "status", "created", "canceled_by__first_name",
                    "canceled_by__last_name", "canceled_by__email", "canceled_by__username",
                )
                .order_by("-created", "-id"),
                to_attr="history_runs",
            )
        )
        .order_by("-created", "-id")
    )
    pages = Paginator(courses, 10).get_page(request.GET.get("page"))
    rows = [(course, history_row(course)) for course in pages]
    if request.headers.get("Accept") == "application/json":
        return JsonResponse({"rows": [row for _, row in rows]})
    return page(request, "history.html", {"courses": pages, "rows": rows})


@require_http_methods(["GET", "POST"])
def manage(request, course_id):
    course = course_for(request.user, course_id, manage=True)
    action = request.POST.get("action")
    title_form = TitleForm(
        request.POST if request.method == "POST" and action == "rename" else None,
        initial={"title": course.title, "description": course.description},
    )
    cover_form = CoverForm(
        request.POST if action == "cover" else None, request.FILES or None,
        initial={"cover_revision": course.updated.isoformat()},
    )
    approval_form = ApprovalForm(
        request.POST if request.method == "POST" and action == "record" else None,
        initial={"revision": course.updated.isoformat()},
    )
    action_form = {"rename": title_form, "cover": cover_form, "record": approval_form}.get(action)
    if request.method == "POST" and (action_form is None or action_form.is_valid()):
        try:
            with transaction.atomic():
                course = Course.objects.select_for_update().get(pk=course.pk)
                if course.runs.filter(status__in=["queued", "running"]).exists():
                    raise ValueError("Wait for the current run to finish.")
                if action == "cover":
                    return update_cover(course, request.user, cover_form.cleaned_data)
                if action == "rename":
                    changed = [
                        key for key in ("title", "description")
                        if getattr(course, key) != title_form.cleaned_data[key]
                    ]
                    if not changed:
                        return redirect("manage", course_id=course.id)
                    course.title = title_form.cleaned_data["title"]
                    course.description = title_form.cleaned_data["description"]
                    if course.manifest:
                        course.manifest = {
                            **course.manifest, "title": course.title,
                            "description": course.description,
                        }
                    run = (
                        new_run(course, request.user, "render")
                        if course.status == "ready" else None
                    )
                    if course.manifest:
                        storage.write_json(f"courses/{course.id}/podcast.json", course.manifest)
                    course.save(update_fields=["title", "description", "manifest", "updated"])
                    record(
                        request.user, "course.updated", course, changed_fields=changed,
                        course_id=str(course.id), group_id=str(course.group_id),
                    )
                    if run:
                        return redirect("run", run_id=run.id)
                    return redirect("manage", course_id=course.id)
                if action == "record":
                    if course.status != "needs_review":
                        raise ValueError("This podcast is not awaiting review.")
                    if not course.manifest.get("episodes"):
                        raise ValueError("There are no transcripts to approve yet.")
                    if approval_form.cleaned_data["revision"] != course.updated.isoformat():
                        raise ValueError(
                            "This podcast changed. Reload and review it before approving."
                        )
                    run = new_run(course, request.user, "record")
                    record(
                        request.user, "course.review_approved", course,
                        changed_fields=["review approval"], course_id=str(course.id),
                        group_id=str(course.group_id), run_id=str(run.id), state="approved",
                    )
                    return redirect("run", run_id=run.id)
                if action == "delete_episode":
                    manifest = deepcopy(course.manifest)
                    episode = next(
                        (
                            episode
                            for episode in manifest.get("episodes", [])
                            if str(episode["number"]) == request.POST.get("episode")
                        ),
                        None,
                    )
                    if episode is None:
                        raise Http404
                    manifest["episodes"].remove(episode)
                    if manifest["episodes"]:
                        run = (
                            new_run(course, request.user, "render")
                            if course.status == "ready" else None
                        )
                        storage.delete_blobs(
                            f"courses/{course.id}/{episode[key]}"
                            for key in ("audio", "transcript")
                            if episode.get(key)
                        )
                        storage.write_json(f"courses/{course.id}/podcast.json", manifest)
                        course.manifest = manifest
                        course.save(update_fields=["manifest", "updated"])
                        record(
                            request.user, "episode.deleted", course,
                            changed_fields=["episodes"], course_id=str(course.id),
                            group_id=str(course.group_id),
                            **({"run_id": str(run.id)} if run else {}),
                        )
                        if run:
                            return redirect("run", run_id=run.id)
                        return redirect("manage", course_id=course.id)
                    action = "delete_course"
                if action == "delete_course":
                    group_id = course.group_id
                    storage.delete_prefix(f"courses/{course.id}")
                    record(
                        request.user, "course.deleted", course, flag=DELETION,
                        course_id=str(course.id), group_id=str(course.group_id),
                    )
                    course.delete()
                    return redirect("library", group_id=group_id)
                raise ValueError("Choose a podcast action.")
        except ValueError as error:
            return error_page(request, 409, str(error))
    episodes = [
        {
            **episode,
            "transcript_url": f"/courses/{course.id}/episodes/{episode['number']}/transcript/"
            if episode.get("transcript")
            else "",
        }
        for episode in course.manifest.get("episodes", [])
    ]
    active_run = course.runs.filter(status__in=["queued", "running"]).first()
    return page(
        request,
        "manage.html",
        {
            "course": course,
            "episodes": episodes,
            "title_form": title_form,
            "cover_form": cover_form,
            "approval_form": approval_form,
            "runs": course.runs.all(),
            "prompt": course.runs.filter(task="create").order_by("created")
            .values_list("request", flat=True).first(),
            "active": active_run is not None,
            "active_run": active_run,
        },
    )


@require_http_methods(["GET", "POST"])
def run_page(request, run_id):
    run = get_object_or_404(Run.objects.select_related("course__group"), pk=run_id)
    course_for(request.user, run.course_id, manage=True)
    if request.method == "POST":
        try:
            with transaction.atomic():
                course = Course.objects.select_for_update().get(pk=run.course_id)
                run = Run.objects.select_for_update().get(pk=run.id)
                action = request.POST.get("action")
                if action == "stop" and run.status in {"queued", "running"}:
                    run.cancel_requested, run.canceled_by = True, request.user
                    if run.status == "queued":
                        run.status, run.finished = "stopped", timezone.now()
                    run.save(
                        update_fields=["cancel_requested", "canceled_by", "status", "finished"]
                    )
                    record(
                        request.user, "run.canceled", run,
                        changed_fields=["cancel_requested"],
                        run_id=str(run.id), course_id=str(course.id),
                        operation_correlation_id=run.correlation_id,
                    )
                    return redirect("run", run_id=run.id)
                if action == "retry" and run.status in {"failed", "stopped"}:
                    if course.runs.filter(status__in=["queued", "running"]).exists():
                        raise ValueError("Wait for the current run to finish.")
                    if not course.manifest:
                        try:
                            course.manifest = storage.read_json(f"courses/{course.id}/podcast.json")
                        except ResourceNotFoundError:
                            pass
                        else:
                            course.save(update_fields=["manifest", "updated"])
                    if course.manifest and (
                        run.text_only or course.status == "needs_review"
                        or course.manifest.get("review", {}).get("approved") is False
                    ):
                        if course.status == "draft":
                            course.status = "needs_review"
                            course.save(update_fields=["status", "updated"])
                            record(
                                request.user, "course.review_restored", course,
                                changed_fields=["status"], course_id=str(course.id),
                                run_id=str(run.id), operation_correlation_id=run.correlation_id,
                            )
                        return redirect("manage", course_id=course.id)
                    task = "record" if course.manifest else "create"
                    retry = new_run(
                        course, request.user, task, run.request, run.sources, run.follow_links,
                        generation_settings=run.generation_settings,
                        text_only="text_only" in request.POST,
                    )
                    return redirect("run", run_id=retry.id)
                raise ValueError("This run cannot perform that action.")
        except ValueError as error:
            return error_page(request, 409, str(error))
    active_run = run.course.runs.filter(status__in=["queued", "running"]).first()
    status_label = {
        "queued": "Waiting to start",
        "running": {
            "create": "Creating your podcast",
            "record": "Recording the audio",
            "render": "Updating the podcast page",
        }[run.task],
        "ready": "Ready to listen",
        "needs_review": "Awaiting your approval",
    }.get(run.status, run.get_status_display())
    if run.cancel_requested and run.status == "running":
        status_label = "Canceling…"
    historical_review = run.status == "needs_review" and (
        run.course.status == "ready" or active_run is not None
    )
    if historical_review:
        status_label = "Completed · transcripts saved for review"
    steps = deepcopy(run.steps)
    if run.status == "needs_review":
        for step in steps:
            if step["key"] in {"record", "done"} and step["status"] == "pending":
                step.update(
                    status="waiting",
                    detail="Not part of this run" if historical_review else "After your approval",
                )
    review_retry = bool(run.course.manifest) and (
        run.text_only or run.course.status == "needs_review"
        or run.course.manifest.get("review", {}).get("approved") is False
    ) and run.course.status != "ready"
    # Raw errors and engine output stay in the admin; they can expose internal details.
    failure = FAILURE_MESSAGE if run.status == "failed" else ""
    if failure and run.error_source == "isa9000" and any(
        problem in run.error for problem in SOURCE_PROBLEMS
    ):
        failure = run.error
    activity = run.log if request.user.is_superuser else []
    if request.headers.get("Accept") == "application/json":
        return JsonResponse(
            {
                "status": run.status,
                "label": status_label,
                "steps": steps,
                "log": activity,
                "error": failure,
                "cancel_requested": run.cancel_requested,
            }
        )
    return page(
        request,
        "run.html",
        {
            "run": run, "messages": activity, "failure": failure, "cancel_warning": CANCEL_WARNING,
            "active": run.status in {"queued", "running"},
            "status_label": status_label, "steps": steps, "course": run.course,
            "review_retry": review_retry,
            "approval_form": ApprovalForm(initial={"revision": run.course.updated.isoformat()}),
            "active_run": active_run,
        },
    )
