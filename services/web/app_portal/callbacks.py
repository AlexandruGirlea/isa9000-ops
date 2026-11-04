from __future__ import annotations

import hmac
import json
from urllib.parse import urlsplit

from core.audit import record
from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from app_portal.models import Course, Run
from shared import payloads, telemetry


def reply(body, run_id, action):
    return JsonResponse(payloads.encrypt(body, f"reply:{run_id}:{action}"))


@csrf_exempt
@require_POST
def callback(request, run_id, action):
    if request.get_host().lower() != urlsplit(settings.CALLBACK_BASE_URL).netloc.lower():
        return JsonResponse({"error": "Not found"}, status=404)
    supplied = request.headers.get("Authorization", "").encode()
    expected = f"Bearer {settings.CALLBACK_SECRET}".encode()
    if not settings.CALLBACK_SECRET or not hmac.compare_digest(supplied, expected):
        telemetry.audit("worker.callback", outcome="denied", run_id=str(run_id))
        return JsonResponse({"error": "Unauthorized"}, status=401)
    request.audit_actor_id = "worker"
    if action not in {"accept", "progress", "result"}:
        return JsonResponse({"error": "Not found"}, status=404)
    try:
        raw = request.read(settings.CALLBACK_MAX_BODY_SIZE + 1)
        if len(raw) > settings.CALLBACK_MAX_BODY_SIZE:
            return JsonResponse({"error": "Callback is too large"}, status=413)
        body = payloads.decrypt(json.loads(raw), f"callback:{run_id}:{action}")
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid callback"}, status=409)
    with transaction.atomic():
        try:
            course_id = Run.objects.values_list("course_id", flat=True).get(pk=run_id)
            course = Course.objects.select_for_update().get(pk=course_id)
            run = Run.objects.select_for_update().get(pk=run_id)
        except (Run.DoesNotExist, Course.DoesNotExist):
            return JsonResponse({"error": "Not found"}, status=404)
        fields = {
            "actor_id": "worker", "run_id": str(run.id), "course_id": str(course.id),
            "group_id": str(course.group_id), "task": run.task,
            "requested_by": run.requested_by.username,
            "operation_correlation_id": run.correlation_id,
        }
        now = timezone.now()
        if action == "accept":
            accepted = run.status == "queued"
            if accepted:
                run.status, run.last_seen = "running", now
                run.save(update_fields=["status", "last_seen"])
                transaction.on_commit(lambda: telemetry.audit("run.accepted", **fields))
            else:
                telemetry.audit("run.accepted", outcome="ignored", **fields)
            return reply({"accepted": accepted}, run_id, action)
        if action == "progress":
            if run.status != "running" or not isinstance(body.get("stage"), str):
                return JsonResponse({"error": "Request is not running"}, status=409)
            stage = "write" if body["stage"] == "verify" else body["stage"]
            message = body.get("message", "")
            if not isinstance(message, str):
                return JsonResponse({"error": "Invalid progress"}, status=409)
            if message:
                run.log.append(message)
            keys = [step["key"] for step in run.steps]
            if stage in keys:
                position = keys.index(stage)
                for index, step in enumerate(run.steps):
                    if index < position:
                        step["status"] = "done"
                    elif index == position:
                        step["status"] = "done" if stage == "done" else "active"
                        current, total = body.get("current"), body.get("total")
                        if current and total:
                            step["detail"] = f"Episode {current} of {total}"
                        elif stage == "plan" and total:
                            step["detail"] = f"{total} episodes planned"
            run.last_seen = now
            run.save(update_fields=["last_seen", "log", "steps"])
            return reply({"cancel": run.cancel_requested}, run_id, action)
        state = body.get("state")
        manifest, html = body.get("manifest"), body.get("html")
        if (
            state not in {"ready", "needs_review", "failed", "stopped"}
            or (manifest is not None and not isinstance(manifest, dict))
            or (html is not None and not isinstance(html, str))
            or (state != "ready" and html is not None)
            or (state == "ready" and not html)
            or not isinstance(body.get("error", ""), str)
            or body.get("error_source", "") not in {"", "isa9000", "worker"}
        ):
            return JsonResponse({"error": "Invalid result"}, status=409)
        if run.status != "running":
            if run.result == body:
                return reply({"ok": True}, run_id, action)
            return JsonResponse({"error": "Request has already finished"}, status=409)
        if manifest is not None:
            course.manifest = manifest
            course.title = manifest.get("title", course.title)
            course.description = manifest.get("description", course.description)
            course.cover = manifest.get("cover") or ""
        if state == "ready":
            course.html = html
        if run.task in {"create", "record"} and state in {"ready", "needs_review"}:
            course.status = state
        course.save()
        run.status, run.finished, run.last_seen = state, now, now
        run.error, run.error_source = body.get("error", ""), body.get("error_source", "")
        run.result = body
        for step in run.steps:
            if state == "ready" or step["status"] == "active":
                step["status"] = "done" if state in {"ready", "needs_review"} else "failed"
        run.save()
        record(
            run.requested_by, "run.finished", run, changed_fields=["status"],
            state=state, **fields,
        )
        if state == "failed":
            telemetry.log(__name__).error(
                "Request failed",
                extra={
                    "state": "failed",
                    "run_id": str(run.id),
                    "course_id": str(course.id),
                    "group_id": str(course.group_id),
                    "task": run.task,
                    "correlation_id": run.correlation_id,
                },
            )
        return reply({"ok": True}, run_id, action)
