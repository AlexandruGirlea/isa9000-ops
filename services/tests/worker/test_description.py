from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

import worker
from shared.descriptions import render_description


def render(description):
    environment = Environment(
        loader=FileSystemLoader(Path(worker.__file__).parent), autoescape=True,
    )
    environment.filters["description_markdown"] = render_description
    template = environment.get_template("course_description.html")
    return template.render(podcast={"description": description})


def test_short_descriptions_show_their_formatting():
    html = render("***bold***\r\n\r\nsecond [link](https://example.com)")
    assert "<strong>" in html
    assert 'href="https://example.com"' in html
    assert "<details" not in html


def test_long_descriptions_collapse_behind_a_plain_excerpt():
    html = render("**word** " * 60)
    assert "<details" in html
    assert 'class="description-excerpt"' in html
    assert "**" not in html


def test_relative_web_and_mail_links_render_and_other_schemes_stay_plain_text():
    for source, href in (
        ("[link](meh)", 'href="meh"'),
        ("[link](/guide/start)", 'href="/guide/start"'),
        ("[link](https://example.com)", 'href="https://example.com"'),
        ("[link](mailto:team@example.com)", 'href="mailto:team@example.com"'),
    ):
        html = render_description(source)
        assert href in html
        assert 'rel="noopener noreferrer"' in html
    for source in (
        "[x](javascript:alert(1))",
        "[x](JaVaScRiPt:alert(1))",
        "[x](java&#115;cript:alert(1))",
        "[x](<\tjavascript:alert(1)>)",
        "[x](data:text/html;base64,AAAA)",
        "[x](vbscript:alert(1))",
    ):
        assert "<a " not in render_description(source), source


def test_images_load_only_from_web_addresses():
    assert 'src="https://example.com/a.png"' in render_description(
        "![a](https://example.com/a.png)"
    )
    for source in ("![a](/.auth/logout)", "![a](meh)"):
        html = render_description(source)
        assert 'src=""' in html
        assert "logout" not in html
