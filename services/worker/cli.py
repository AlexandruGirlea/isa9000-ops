from __future__ import annotations

import runpy

from isa9000 import links, sources

from worker.source_http import SourceHTTP

if __name__ == "__main__":
    transport = SourceHTTP()
    links.requests = transport
    sources.requests = transport
    runpy.run_module("isa9000", run_name="__main__")
