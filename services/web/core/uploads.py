from __future__ import annotations

from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.core.files.uploadhandler import FileUploadHandler
from django.http import HttpResponse


class UploadTooLarge(RequestDataTooBig):
    pass


class BodyLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        limit = settings.DATA_UPLOAD_MAX_MEMORY_SIZE
        if request.content_type == "multipart/form-data":
            limit += settings.MAX_UPLOAD_MB * settings.MAX_FILES * 1024 * 1024
        elif request.path.startswith("/internal/"):
            limit = settings.CALLBACK_MAX_BODY_SIZE
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except ValueError:
            return HttpResponse(status=400)
        if length < 0:
            return HttpResponse(status=400)
        if length > limit:
            return HttpResponse(status=413)
        return self.get_response(request)


class LimitedUploadHandler(FileUploadHandler):
    def __init__(self, request=None):
        super().__init__(request)
        self.total = 0

    def receive_data_chunk(self, raw_data, start):
        self.total += len(raw_data)
        maximum = settings.MAX_UPLOAD_MB * 1024 * 1024
        if start + len(raw_data) > maximum or self.total > maximum * settings.MAX_FILES:
            # Django's parse-error cleanup only closes files whose uploads have completed.
            for handler in self.request.upload_handlers:
                if file := getattr(handler, "file", None):
                    file.close()
            raise UploadTooLarge(f"Each uploaded file must be at most {settings.MAX_UPLOAD_MB} MB.")
        return raw_data

    def file_complete(self, file_size):
        return None
