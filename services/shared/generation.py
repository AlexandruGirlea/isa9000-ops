from __future__ import annotations

import string
from typing import Literal

from isa9000.config import PLACEHOLDERS, VOICES, Config
from pydantic import BaseModel, ConfigDict, Field, field_validator


class GenerationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=120)
    voice: str = Field(min_length=1, max_length=120)
    greeting: str = Field(max_length=2000)
    ending: str = Field(max_length=2000)
    final_ending: str = Field(max_length=2000)
    pronunciations: dict[str, str] = Field(max_length=100)
    quality: Literal["quick", "standard", "thorough"]

    @field_validator("name")
    @classmethod
    def narrator_name(cls, value):
        if not value.strip():
            raise ValueError("Enter a narrator name.")
        return value.strip()

    @field_validator("greeting", "ending", "final_ending")
    @classmethod
    def placeholders(cls, value):
        for _, name, format_spec, conversion in string.Formatter().parse(value):
            if name is not None and (
                name not in PLACEHOLDERS or format_spec or conversion is not None
            ):
                raise ValueError("Use only the listed placeholders, without formatting.")
        return value

    @field_validator("pronunciations")
    @classmethod
    def pronunciation_lengths(cls, value):
        if any(
            not term.strip() or not spoken.strip() or len(term) > 200 or len(spoken) > 200
            for term, spoken in value.items()
        ):
            raise ValueError("Each pronunciation needs a word and spelling, up to 200 characters.")
        return value


def generation_defaults(config: Config) -> dict:
    return {
        "name": config.narrator.name,
        "voice": config.narrator.voice,
        "greeting": config.branding.greeting,
        "ending": config.branding.ending,
        "final_ending": config.branding.final_ending,
        "pronunciations": dict(config.narrator.pronunciations),
        "quality": config.output.quality,
    }


def validate_generation(data: dict, config: Config) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Podcast settings must be an object.")
    values = GenerationSettings(**(generation_defaults(config) | data)).model_dump()
    if values["voice"] not in {*VOICES[config.provider], config.narrator.voice}:
        raise ValueError("Choose an available narrator voice.")
    return values
