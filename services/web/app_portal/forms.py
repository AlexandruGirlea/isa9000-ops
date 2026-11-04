from __future__ import annotations

from pathlib import Path

from core.security import source_url
from django import forms
from django.conf import settings
from isa9000.config import VOICES, load_config
from pydantic import ValidationError

from shared.generation import generation_defaults, validate_generation


class UploadWidget(forms.ClearableFileInput):
    allow_multiple_selected = True


class UploadField(forms.FileField):
    def clean(self, data, initial=None):
        if not data:
            return []
        files = data if isinstance(data, (list, tuple)) else [data]
        if len(files) > settings.MAX_FILES:
            raise forms.ValidationError(f"Upload at most {settings.MAX_FILES} files.")
        for upload in files:
            super().clean(upload, initial)
            if Path(upload.name).suffix.lower() not in {".pdf", ".txt", ".md", ".html"}:
                raise forms.ValidationError("Upload PDF, TXT, MD or HTML files.")
            if upload.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
                raise forms.ValidationError(
                    f"Each file must be at most {settings.MAX_UPLOAD_MB} MB."
                )
        return files


class GenerationForm(forms.Form):
    name = forms.CharField(max_length=120, label="Narrator name", required=False)
    voice = forms.ChoiceField(label="Voice", required=False)
    greeting = forms.CharField(max_length=2000, required=False, label="Episode greeting")
    ending = forms.CharField(max_length=2000, required=False, label="Episode ending")
    final_ending = forms.CharField(max_length=2000, required=False, label="Final episode ending")
    quality = forms.ChoiceField(
        required=False,
        choices=[
            ("quick", "Quick draft · fewer checks"),
            ("standard", "Standard · recommended"),
            ("thorough", "Thorough · more checks, takes longer"),
        ],
    )
    pronunciations = forms.CharField(
        required=False,
        max_length=40500,
        widget=forms.Textarea(
            attrs={"rows": 3, "placeholder": "Contoso = con-TOH-so\nAPI = A P I"}
        ),
        label="Pronunciations",
    )

    def __init__(self, *args, generation_settings=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = load_config()
        self.defaults = generation_defaults(self.config) | (generation_settings or {})
        voices = dict.fromkeys([self.config.narrator.voice, *VOICES[self.config.provider]])
        self.fields["voice"].choices = [(voice, voice) for voice in voices]
        self.initial.update(self.defaults)
        self.initial["pronunciations"] = "\n".join(
            f"{term} = {spoken}" for term, spoken in self.defaults["pronunciations"].items()
        )

    def clean_pronunciations(self):
        if "pronunciations" not in self.data:
            return self.defaults["pronunciations"]
        pronunciations = {}
        for line in self.cleaned_data["pronunciations"].splitlines():
            if not line.strip():
                continue
            term, separator, spoken = line.partition("=")
            term, spoken = term.strip(), spoken.strip()
            if not separator or not term or not spoken:
                raise forms.ValidationError("Use one pronunciation per line: word = how to say it.")
            if term in pronunciations:
                raise forms.ValidationError("Use each word only once in pronunciations.")
            pronunciations[term] = spoken
        return pronunciations

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        values = {
            key: cleaned[key] if key in self.data else value
            for key, value in self.defaults.items()
        }
        try:
            cleaned["generation_settings"] = validate_generation(values, self.config)
        except ValidationError as error:
            for problem in error.errors():
                key = problem["loc"][0]
                self.add_error(key, problem["msg"].removeprefix("Value error, "))
        except ValueError as error:
            self.add_error("voice", str(error))
        return cleaned


class CreateForm(GenerationForm):
    title = forms.CharField(
        max_length=80, label="Podcast title",
        widget=forms.TextInput(attrs={
            "placeholder": "For example: Getting started with our team",
            "aria-describedby": "title-help",
        }),
    )
    description = forms.CharField(
        max_length=10000, required=False, label="Description",
        widget=forms.Textarea(attrs={
            "rows": 5, "placeholder": "What will listeners learn?\n\n"
            "Useful reading:\n- [Team handbook](https://example.com/handbook)",
            "aria-describedby": "description-help",
        }),
    )
    request = forms.CharField(
        max_length=12000,
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "For example: what changed in our cloud vendors this month, "
                "and what does it mean for my team?",
            }
        ),
        label="What should this podcast cover?",
    )
    links = forms.CharField(
        required=False,
        max_length=40000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "https://…"}),
        label="Source links, one per line",
    )
    uploads = UploadField(
        required=False, widget=UploadWidget(attrs={"accept": ".pdf,.txt,.md,.html"})
    )
    follow_links = forms.BooleanField(required=False, label="Also read linked pages")
    text_only = forms.BooleanField(
        required=False, initial=True, label="Review transcripts before recording",
    )

    def clean_links(self):
        links = list(dict.fromkeys(
            line.strip() for line in self.cleaned_data["links"].splitlines() if line.strip()
        ))
        if len(links) > 20:
            raise forms.ValidationError("Add at most 20 source links.")
        return [source_url(link) for link in links]


class TitleForm(forms.Form):
    title = forms.CharField(
        max_length=80, label="Podcast title",
        widget=forms.TextInput(attrs={"aria-describedby": "title-help"}),
    )
    description = forms.CharField(
        max_length=10000, required=False, label="Description",
        widget=forms.Textarea(attrs={"rows": 5, "aria-describedby": "description-help"}),
    )


class ApprovalForm(forms.Form):
    revision = forms.CharField(max_length=40, widget=forms.HiddenInput)
    approved = forms.BooleanField(
        label="I have reviewed the transcripts and approve them for recording and publication.",
        error_messages={"required": "Confirm your approval before recording."},
    )
