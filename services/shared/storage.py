from __future__ import annotations

import json
import mimetypes
import os
from collections.abc import Iterable, Iterator
from functools import cache
from pathlib import Path
from typing import BinaryIO

from azure.core import MatchConditions
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import (
    BlobProperties,
    BlobServiceClient,
    ContentSettings,
)


@cache
def _client() -> BlobServiceClient:
    connection = os.environ.get("ISA9000_STORAGE_CONNECTION_STRING")
    transfer = {"max_single_get_size": 1024 * 1024, "max_chunk_get_size": 1024 * 1024}
    if connection:
        return BlobServiceClient.from_connection_string(connection, **transfer)
    account = os.environ["ISA9000_STORAGE_ACCOUNT"]
    return BlobServiceClient(
        f"https://{account}.blob.core.windows.net", credential=DefaultAzureCredential(),
        **transfer,
    )


def _split(name: str) -> tuple[str, str]:
    parts = name.split("/")
    if (
        len(parts) < 2
        or parts[0] not in {"courses", "uploads"}
        or any(part in {"", ".", ".."} for part in parts)
        or "\\" in name
        or any(ord(char) < 32 for char in name)
    ):
        raise ValueError("Invalid blob name.")
    return parts[0], "/".join(parts[1:])


def _destination(local: Path, relative: str) -> Path:
    root = local.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or target == root:
        raise ValueError("Blob path escapes the download folder.")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def save_upload(file: BinaryIO | bytes, name: str) -> str:
    container, blob = _split(name)
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    _client().get_blob_client(container, blob).upload_blob(
        file, overwrite=True, content_settings=ContentSettings(content_type=content_type)
    )
    return name


def upload_folder(local: str | Path, prefix: str) -> None:
    local = Path(local).resolve()
    prefix = prefix.rstrip("/")
    _split(prefix)
    for path in sorted(local.rglob("*")):
        if path.is_symlink():
            raise ValueError("Course folders must not contain symbolic links.")
        if path.is_file():
            with path.open("rb") as file:
                save_upload(file, f"{prefix}/{path.relative_to(local).as_posix()}")


def download_prefix(prefix: str, local: str | Path) -> list[Path]:
    container, blob_prefix = _split(prefix.rstrip("/"))
    blob_prefix += "/"
    client = _client().get_container_client(container)
    paths = []
    for blob in client.list_blobs(name_starts_with=blob_prefix):
        _split(f"{container}/{blob.name}")
        if not blob.name.startswith(blob_prefix):
            raise ValueError("Blob is outside the requested prefix.")
        target = _destination(Path(local), blob.name[len(blob_prefix):])
        with target.open("wb") as file:
            client.download_blob(blob.name).readinto(file)
        paths.append(target)
    return paths


def download_blobs(names: Iterable[str], local: str | Path) -> list[Path]:
    paths = []
    for name in names:
        container, blob = _split(name)
        if container != "uploads":
            raise ValueError("Sources must be blobs under uploads/.")
        target = _destination(Path(local), name)
        with target.open("wb") as file:
            _client().get_blob_client(container, blob).download_blob().readinto(file)
        paths.append(target)
    return paths


def delete_prefix(prefix: str) -> None:
    container, blob_prefix = _split(prefix.rstrip("/"))
    blobs = _client().get_container_client(container).list_blobs(
        name_starts_with=blob_prefix + "/"
    )
    delete_blobs(f"{container}/{blob.name}" for blob in blobs)


def delete_blobs(names: Iterable[str]) -> None:
    for name in names:
        container, blob = _split(name)
        try:
            _client().get_blob_client(container, blob).delete_blob(delete_snapshots="include")
        except ResourceNotFoundError:
            pass


def blob_properties(name: str) -> BlobProperties:
    container, blob = _split(name)
    return _client().get_blob_client(container, blob).get_blob_properties()


def open_blob(
    name: str, *, offset: int | None = None, length: int | None = None,
    etag: str | None = None,
) -> Iterator[bytes]:
    container, blob = _split(name)
    conditions = {"etag": etag, "match_condition": MatchConditions.IfNotModified} if etag else {}
    return _client().get_blob_client(container, blob).download_blob(
        offset=offset, length=length, max_concurrency=1, **conditions,
    ).chunks()


def read_json(name: str) -> dict:
    container, blob = _split(name)
    return json.loads(_client().get_blob_client(container, blob).download_blob().readall())


def read_text(name: str, max_bytes: int = 1024 * 1024) -> str:
    container, blob = _split(name)
    if max_bytes <= 0:
        raise ValueError("Text size limit must be positive.")
    data = _client().get_blob_client(container, blob).download_blob(
        offset=0, length=max_bytes + 1, max_concurrency=1,
    ).readall()
    if len(data) > max_bytes:
        raise ValueError("The text exceeds the size limit.")
    return data.decode("utf-8")


def write_json(name: str, data: dict) -> None:
    save_upload(json.dumps(data, ensure_ascii=False).encode("utf-8"), name)
