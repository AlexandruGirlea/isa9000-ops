from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from azure.storage.blob import BlobServiceClient

from shared import storage


@pytest.fixture(autouse=True)
def payload_key(monkeypatch):
    monkeypatch.setenv("ISA9000_PAYLOAD_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")


@pytest.fixture
def stub_cli() -> list[str]:
    return [sys.executable, str(Path(__file__).with_name("stub_isa9000.py"))]


@pytest.fixture
def branding_config() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "isa9000.toml"


@pytest.fixture
def blob_service(monkeypatch):
    client = MagicMock(spec=BlobServiceClient)
    client.account_name = "testaccount"
    client.url = "https://testaccount.blob.core.windows.net/"
    monkeypatch.setattr(storage, "_client", lambda: client)
    monkeypatch.delenv("ISA9000_STORAGE_CONNECTION_STRING", raising=False)
    monkeypatch.delenv("ISA9000_STORAGE_PUBLIC_URL", raising=False)
    return client
