from __future__ import annotations

import re
from importlib.resources import files

from markdown_it import MarkdownIt
from markupsafe import Markup
from mdit_py_plugins.footnote import footnote_plugin

DESCRIPTION_CSS = files("shared").joinpath("description.css").read_text()
_markdown = MarkdownIt("gfm-like2", {"html": False}).use(footnote_plugin)
# Web, mail and relative links only: every other scheme, such as javascript:, stays plain text.
SAFE_LINK = re.compile(r"https?://|mailto:|[^:\s\x00-\x1f]*(?:[/?#]|$)", re.IGNORECASE)
_markdown.validateLink = lambda url: SAFE_LINK.match(url) is not None


def render_description(source: str) -> Markup:
    tokens = _markdown.parse(source)
    for token in tokens:
        for child in token.children or []:
            if child.type == "link_open":
                child.attrSet("target", "_blank")
                child.attrSet("rel", "noopener noreferrer")
            elif child.type == "image":
                # A same-site image URL would make every reader's browser request that page.
                if not re.match(r"https?://", child.attrGet("src") or "", re.IGNORECASE):
                    child.attrSet("src", "")
                child.attrSet("loading", "lazy")
                child.attrSet("referrerpolicy", "no-referrer")
    return Markup(_markdown.renderer.render(tokens, _markdown.options, {}))
