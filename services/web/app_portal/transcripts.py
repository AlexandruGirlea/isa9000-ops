from __future__ import annotations

import json
import uuid

from azure.core.exceptions import AzureError, ResourceNotFoundError
from core.audit import record
from django.db import transaction
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_http_methods

from app_portal.models import Course
from app_portal.permissions import course_for
from shared import storage


@require_http_methods(["GET", "POST"])
def transcript(request, course_id, episode_number):
    course = course_for(request.user, course_id, manage=True)
    data = None
    if request.method == "POST":
        try:
            if int(request.META.get("CONTENT_LENGTH") or 0) > 1024 * 1024:
                return JsonResponse({"error": "The transcript is too long."}, status=413)
            body = request.body
            if len(body) > 1024 * 1024:
                return JsonResponse({"error": "The transcript is too long."}, status=413)
            data = json.loads(body)
            if not isinstance(data, dict) or not isinstance(data.get("text"), str):
                raise ValueError
            text = data["text"].replace("\r\n", "\n")
            encoded = text.encode("utf-8")
            if len(text) > 100000 or len(encoded) > 1024 * 1024:
                return JsonResponse({"error": "Use at most 100,000 characters."}, status=413)
            if not text.strip() or any(ord(char) < 32 and char not in "\n\r\t" for char in text):
                raise ValueError
        except (ValueError, UnicodeError):
            return JsonResponse({"error": "Enter a nonempty text transcript."}, status=400)
    try:
        with transaction.atomic():
            if data is not None:
                course = Course.objects.select_for_update().get(pk=course.pk)
            episode = next(
                (
                    entry for entry in course.manifest.get("episodes", [])
                    if isinstance(entry, dict) and entry.get("number") == episode_number
                ),
                None,
            )
            filename = episode.get("transcript") if episode else None
            if (
                not isinstance(filename, str) or filename in {"", ".", ".."}
                or "/" in filename or "\\" in filename
                or any(ord(char) < 32 for char in filename)
            ):
                raise Http404
            active = course.runs.filter(status__in=["queued", "running"]).exists()
            revision = course.updated.isoformat()
            if data is not None:
                if active:
                    return JsonResponse(
                        {"error": "Wait for the current run to finish before editing."}, status=409,
                    )
                if data.get("revision") != revision:
                    return JsonResponse(
                        {"error": "This podcast changed. Reload it before saving your edits."},
                        status=409,
                    )
                if course.status == "ready" and data.get("unpublish") is not True:
                    return JsonResponse(
                        {"error": "Confirm that this podcast will leave the library until "
                         "you approve and record the edited transcript."}, status=409,
                    )
            current = storage.read_text(f"courses/{course.id}/{filename}")
            changed = data is not None and text != current
            if changed:
                filename = f"{episode_number:02d} - revision-{uuid.uuid4().hex}.txt"
                storage.save_upload(encoded, f"courses/{course.id}/{filename}")
                episode.update(transcript=filename, audio=None, seconds=None, edited=True)
                episode.pop("audio_note", None)
                course.manifest["review"] = {
                    **course.manifest.get("review", {}),
                    "approved": False, "fact_checked": False, "edited": True,
                }
                course.status, course.html = "needs_review", ""
                course.save(update_fields=["manifest", "status", "html", "updated"])
                record(
                    request.user, "transcript.updated", course,
                    changed_fields=[f"episodes.{episode_number}.transcript", "review", "status"],
                    course_id=str(course.id), group_id=str(course.group_id), state="needs_review",
                )
            title = episode.get("title")
            return JsonResponse({
                "title": title if isinstance(title, str) and title else f"Episode {episode_number}",
                "text": text if data is not None else current,
                "revision": course.updated.isoformat(), "changed": changed,
                "editable": not active, "published": course.status == "ready",
                "edit_reason": "Editing is locked while a run is in progress." if active else "",
            })
    except ResourceNotFoundError:
        raise Http404 from None
    except (UnicodeDecodeError, ValueError):
        return JsonResponse({"error": "This transcript cannot be displayed."}, status=422)
    except AzureError:
        return JsonResponse(
            {"error": "The transcript is temporarily unavailable. Please try again."}, status=503,
        )
