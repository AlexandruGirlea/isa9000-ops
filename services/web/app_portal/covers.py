from __future__ import annotations

import uuid
import warnings
from io import BytesIO
from pathlib import Path

from core.audit import record
from django import forms
from django.shortcuts import redirect
from PIL import Image, ImageOps, UnidentifiedImageError

from app_portal.runs import new_run
from shared import storage

MAX_COVER_BYTES = 5 * 1024 * 1024
MIN_COVER_SIDE = 256
MAX_COVER_SIDE = 4096
COVER_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


class CoverForm(forms.Form):
    cover_revision = forms.CharField(max_length=40, widget=forms.HiddenInput)
    cover = forms.FileField(
        label="Choose a cover photo or image",
        widget=forms.FileInput(attrs={
            "accept": "image/jpeg,image/png,image/webp",
            "aria-describedby": "cover-help cover-error",
            "data-max-bytes": MAX_COVER_BYTES,
            "data-min-side": MIN_COVER_SIDE,
            "data-max-side": MAX_COVER_SIDE,
        }),
    )

    def clean_cover(self):
        upload = self.cleaned_data["cover"]
        if upload.size > MAX_COVER_BYTES:
            raise forms.ValidationError("Use an image of at most 5 MiB.")
        if Path(upload.name).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise forms.ValidationError("Use a JPEG, PNG or WebP image.")
        data = upload.read(MAX_COVER_BYTES + 1)
        if len(data) > MAX_COVER_BYTES:
            raise forms.ValidationError("Use an image of at most 5 MiB.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(data), formats=list(COVER_FORMATS)) as source:
                    image_format = source.format
                    width, height = source.size
                    if width != height or not MIN_COVER_SIDE <= width <= MAX_COVER_SIDE:
                        raise forms.ValidationError(
                            "Use a square image between 256 and 4096 pixels on each side."
                        )
                    if getattr(source, "n_frames", 1) != 1:
                        raise forms.ValidationError(
                            "Use a still image; animations are not supported."
                        )
                    source.verify()
                with Image.open(BytesIO(data), formats=list(COVER_FORMATS)) as source:
                    source.load()
                    oriented = ImageOps.exif_transpose(source)
                    oriented.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                    mode = "RGB" if image_format == "JPEG" else "RGBA"
                    with Image.new(mode, oriented.size) as clean:
                        clean.paste(oriented.convert(mode))
                        output = BytesIO()
                        clean.save(output, format=image_format, quality=90)
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
                Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise forms.ValidationError(
                "This image cannot be read. Use a valid JPEG, PNG or WebP."
            ) from error
        if output.tell() > MAX_COVER_BYTES:
            raise forms.ValidationError(
                "The processed image exceeds 5 MiB. Choose a smaller image."
            )
        return output.getvalue(), COVER_FORMATS[image_format]


def update_cover(course, user, values):
    if values["cover_revision"] != course.updated.isoformat():
        raise ValueError("This podcast changed. Reload it before changing the cover.")
    if not course.manifest.get("episodes"):
        raise ValueError("Wait until this podcast has episodes before changing the cover.")
    data, extension = values["cover"]
    filename = f"cover-{uuid.uuid4().hex}{extension}"
    course.manifest = {**course.manifest, "cover": filename}
    run = new_run(course, user, "render") if course.status == "ready" else None
    storage.save_upload(data, f"courses/{course.id}/{filename}")
    storage.write_json(f"courses/{course.id}/podcast.json", course.manifest)
    if not run:
        course.cover = filename
    course.save(update_fields=["cover", "manifest", "updated"])
    record(
        user, "course.cover_updated", course, changed_fields=["cover"],
        course_id=str(course.id), group_id=str(course.group_id),
        **({"run_id": str(run.id)} if run else {}),
    )
    return redirect("run", run_id=run.id) if run else redirect("manage", course_id=course.id)
