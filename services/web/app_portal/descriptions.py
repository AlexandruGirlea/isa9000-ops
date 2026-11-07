from __future__ import annotations

from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_POST

from app_portal.forms import TitleForm
from app_portal.permissions import course_for, group_for, is_creator
from shared.descriptions import render_description


@require_POST
def preview(request, group_id=None, course_id=None):
    if course_id is not None:
        course_for(request.user, course_id, manage=True)
    elif group_id is not None:
        group_for(request.user, group_id, manage=True)
    elif not is_creator(request.user):
        raise Http404
    try:
        size = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        size = 128001
    if size > 128000:
        return JsonResponse({"error": "Description is too long."}, status=400)
    try:
        description = TitleForm.base_fields["description"].clean(
            request.POST.get("description", "")
        )
    except ValidationError as error:
        return JsonResponse({"error": error.messages[0]}, status=400)
    return JsonResponse({"html": str(render_description(description))})
