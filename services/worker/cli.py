from __future__ import annotations

import os
import runpy
import time
from pathlib import Path

import azure.identity
from azure.core.credentials import AccessToken
from isa9000 import links, sources

from worker.source_http import SourceHTTP


class FoundryToken:
    def get_token(self, *scopes: str, **kwargs: object) -> AccessToken:
        token = Path(os.environ["ISA9000_FOUNDRY_TOKEN_FILE"]).read_text(encoding="utf-8")
        # Expires early so the client rereads the file, which the worker refreshes every minute.
        return AccessToken(token, int(time.time()) + 360)


if __name__ == "__main__":
    transport = SourceHTTP()
    links.requests = transport
    sources.requests = transport
    if "ISA9000_FOUNDRY_TOKEN_FILE" in os.environ:
        azure.identity.DefaultAzureCredential = FoundryToken
    runpy.run_module("isa9000", run_name="__main__")
