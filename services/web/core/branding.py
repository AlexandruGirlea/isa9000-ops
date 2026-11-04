from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote

from isa9000.config import DEFAULT_COLOR, DEFAULT_MARKS, LOGO_TYPES, load_config
from jinja2 import ChoiceLoader, DictLoader, Environment, PackageLoader
from markupsafe import Markup

from shared.descriptions import DESCRIPTION_CSS, render_description


def site():
    config = load_config()
    logo = "logo.svg" if config.branding.logo else "logo.png"
    color = config.branding.accent_color or DEFAULT_COLOR
    return {
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
        "marks": {"on_dark": logo, "on_light": logo}
        if config.branding.logo
        else {key: path.name for key, path in DEFAULT_MARKS.items()},
        "logo_type": LOGO_TYPES[Path(logo).suffix],
        "feed": None,
    }


def environment(**options):
    package = PackageLoader("isa9000", "presentation_website_templates")
    base = package.get_source(None, "base.html")[0]
    options["loader"] = ChoiceLoader(
        [
            options["loader"],
            DictLoader({"base.html": base.replace(
                "<script>", '<script nonce="{{ request.csp_nonce }}">',
            )}),
            package,
        ]
    )
    env = Environment(**options)
    env.filters["description_markdown"] = render_description
    env.globals["description_css"] = Markup(DESCRIPTION_CSS)
    env.globals["generation_css"] = Markup(
        files("isa9000").joinpath("audio_generation_ui/static/theme.css").read_text()
        .replace("/assets/", "/static/isa9000/")
    )
    catalog = env.get_template("index.html")
    env.globals["catalog_css"] = Markup(
        "".join(catalog.blocks["extra_css"](catalog.new_context({})))
    )
    return env


def player_html(html, request, course):
    templates = files("isa9000").joinpath("presentation_website_templates")
    pattern = r"<script\b[^>]*>.*?</script\s*>"
    scripts = [
        script
        for name in ("base.html", "podcast.html")
        for script in re.findall(pattern, templates.joinpath(name).read_text(), flags=re.S | re.I)
    ]
    env = Environment(autoescape=True)
    context = {
        "request": request, "site": site(), "base_path": "/static/isa9000/",
        "podcast": {
            "title": course.title,
            "cover_url": f"/media/{course.id}/{quote(course.cover)}" if course.cover else "",
        },
    }
    trusted = [
        env.from_string(script.replace(
            "<script>", '<script nonce="{{ request.csp_nonce }}">',
        )).render(context)
        for script in scripts
    ]
    html = re.sub(pattern, "", html, flags=re.S | re.I)
    return html.replace("</head>", trusted[0] + "</head>", 1).replace(
        "</body>", "".join(trusted[1:]) + "</body>", 1,
    )
