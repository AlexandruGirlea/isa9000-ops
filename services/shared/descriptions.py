from __future__ import annotations

import re
from importlib.resources import files

from markdown_it import MarkdownIt
from markupsafe import Markup
from mdit_py_plugins.footnote import footnote_plugin

DESCRIPTION_CSS = files("shared").joinpath("description.css").read_text()
_markdown = MarkdownIt("gfm-like2", {"html": False}).use(footnote_plugin)
_markdown.validateLink = lambda url: bool(re.match(r"https?://", url, re.IGNORECASE))


def render_description(source: str) -> Markup:
    tokens = _markdown.parse(source)
    for token in tokens:
        for child in token.children or []:
            if child.type == "link_open":
                child.attrSet("target", "_blank")
                child.attrSet("rel", "noopener noreferrer")
            elif child.type == "image":
                child.attrSet("loading", "lazy")
                child.attrSet("referrerpolicy", "no-referrer")
    return Markup(_markdown.renderer.render(tokens, _markdown.options, {}))
