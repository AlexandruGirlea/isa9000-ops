from __future__ import annotations

import re

from azure.core.exceptions import ResourceModifiedError, ResourceNotFoundError
from django.http import Http404, HttpResponse, StreamingHttpResponse
from django.views.decorators.http import require_safe

from app_portal.permissions import course_for
from shared import storage


@require_safe
def media(request, course_id, filename):
    course = course_for(request.user, course_id)
    files = {course.cover}
    for episode in course.manifest.get("episodes", []):
        files.update((episode.get("audio"), episode.get("transcript")))
    if filename not in files or "/" in filename or "\\" in filename or filename in {".", ".."}:
        raise Http404
    name = f"courses/{course.id}/{filename}"
    try:
        properties = storage.blob_properties(name)
        size = properties.size
        start, end, status = 0, size - 1, 200
        requested = request.headers.get("Range", "") if request.method == "GET" else ""
        if request.headers.get("If-Range", properties.etag) != properties.etag:
            requested = ""
        if requested:
            match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", requested[:200])
            try:
                if not match or len(requested) > 200 or not any(match.groups()):
                    raise ValueError
                first, last = match.groups()
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                else:
                    start = max(0, size - int(last))
                if start > end or start >= size:
                    raise ValueError
            except ValueError:
                response = HttpResponse(status=416, content_type="application/octet-stream")
                response["Content-Range"] = f"bytes */{size}"
                response["Accept-Ranges"] = "bytes"
                response["Cache-Control"] = "private, no-store"
                return response
            status = 206
        length = end - start + 1
        content_type = properties.content_settings.content_type or "application/octet-stream"
        if request.method == "HEAD" or not length:
            response = HttpResponse(content_type=content_type, status=status)
        else:
            response = StreamingHttpResponse(
                storage.open_blob(name, offset=start, length=length, etag=properties.etag),
                content_type=content_type, status=status,
            )
    except ResourceNotFoundError as error:
        raise Http404 from error
    except ResourceModifiedError:
        return HttpResponse(status=412)
    response["Content-Length"] = length
    response["Accept-Ranges"] = "bytes"
    response["ETag"] = properties.etag
    response["Cache-Control"] = "private, no-store"
    if status == 206:
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    return response
