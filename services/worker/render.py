from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from isa9000.config import DEFAULT_COLOR, LOGO_TYPES, Config
from isa9000.presentation_website import _collect, _logo, _marks, _page
from jinja2 import ChoiceLoader, DictLoader, Environment, PackageLoader
from markupsafe import Markup

from shared.descriptions import DESCRIPTION_CSS, render_description


def render_course(
    folder: Path, site_root: Path, manifest: dict, links: dict, config: Config
) -> str:
    library = _collect(folder.parent, site_root)
    if len(library) != 1:
        raise ValueError("A course must contain at least one recorded episode.")
    page = _page(library[0], site_root)
    episodes = {entry["number"]: entry for entry in manifest["episodes"]}
    for episode in page["episodes"]:
        episode["audio_url"] = links["media"] + quote(episodes[episode["number"]]["audio"])
    page["cover_url"] = links["media"] + quote(manifest["cover"]) if manifest.get("cover") else ""
    logo = _logo(config, site_root)
    color = config.branding.accent_color or DEFAULT_COLOR
    site = {
        "title": config.presentation_website.title or config.branding.brand,
        "heading": config.presentation_website.heading,
        "description": config.presentation_website.description,
        "brand": config.branding.brand,
        "powered_by": Markup(
            '<a href="https://github.com/AlexandruGirlea/isa9000" style="color: inherit">{}</a>'
        ).format(config.branding.powered_by)
        if config.branding.powered_by
        else "",
        "color": color,
        "custom_color": color if color.lower() != DEFAULT_COLOR.lower() else "",
        "logo": logo,
        "marks": _marks(config, logo, site_root),
        "logo_type": LOGO_TYPES[Path(logo).suffix] if logo else "",
        "feed": None,
    }
    templates = Environment(
        loader=PackageLoader("isa9000", "presentation_website_templates"), autoescape=True
    )
    source = templates.loader.get_source(templates, "podcast.html")[0]
    description = '<p class="mt-3">{{ podcast.description }}</p>'
    if source.count(description) != 1:
        raise ValueError("The podcast template's description slot has changed.")
    templates.loader = ChoiceLoader([
        DictLoader({
            "podcast.html": source.replace(
                description, '{% include "course_description.html" %}',
            ),
            "course_description.html": Path(__file__).with_name(
                "course_description.html"
            ).read_text(),
        }),
        templates.loader,
    ])
    templates.filters["description_markdown"] = render_description
    return templates.from_string(
        """{% extends "podcast.html" %}
{% block extra_css %}{{ super() }}
{{ description_css }}
.course-breadcrumbs { padding: 100px 0 0; background: var(--darker); }
.course-breadcrumbs .container { display: flex; flex-wrap: wrap; gap: 8px;
  font-size: .9rem; color: rgba(255,255,255,.7); overflow-wrap: anywhere; }
.course-breadcrumbs a { color: var(--primary-light); text-decoration: none; }
.course-breadcrumbs a:hover { text-decoration: underline; }
.podcast-hero { padding-top: 24px; }
.podcast-hero h1 { overflow-wrap: anywhere; }
.podcast-hero .back-link { display: none; }
.podcast-description { overflow-wrap: anywhere; }
.description-excerpt { display: -webkit-box; -webkit-box-orient: vertical;
  -webkit-line-clamp: 3; overflow: hidden; }
.description-details > summary { color: var(--primary-light); min-height: 44px; }
.podcast-description:has(details[open]) > .description-excerpt { display: none; }
@media (max-width: 575px) { .podcast-hero .podcast-cover { max-width: 120px; } }
{% endblock %}
{% block content %}
<nav class="course-breadcrumbs" aria-label="Breadcrumb"><div class="container">
<a href="/libraries/">Libraries</a><span>&rsaquo;</span>
<a href="{{ nav_path }}">{{ library_name }}</a><span>&rsaquo;</span>
<span>{{ podcast.title }}</span>
</div></nav>
{{ super() }}
{% endblock %}"""
    ).render(
        site=site,
        podcast=page,
        base_path=links["static"],
        nav_path=links["home"],
        library_name=links.get("library", "Library"),
        description_css=Markup(DESCRIPTION_CSS),
    )
