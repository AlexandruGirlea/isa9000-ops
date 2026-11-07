from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import ResourceNotFoundError

from shared import storage


def test_upload_and_json(blob_service, tmp_path):
    local = tmp_path / "course"
    local.mkdir()
    (local / "01 - Welcome.mp3").write_bytes(b"audio")
    copied = {}

    def upload(data, **kwargs):
        copied[blob_service.get_blob_client.call_args.args] = (
            data.read() if hasattr(data, "read") else data,
            kwargs["content_settings"].content_type,
        )

    client = blob_service.get_blob_client.return_value
    client.upload_blob.side_effect = upload
    storage.upload_folder(local, "courses/course-1/")
    storage.save_upload(io.BytesIO(b"handbook"), "uploads/run-1/guide.txt")
    storage.write_json("courses/course-1/podcast.json", {"title": "Résumé"})
    assert copied[("courses", "course-1/01 - Welcome.mp3")] == (b"audio", "audio/mpeg")
    assert copied[("uploads", "run-1/guide.txt")] == (b"handbook", "text/plain")
    download = client.download_blob.return_value
    download.readall.return_value = copied[("courses", "course-1/podcast.json")][0]
    assert storage.read_json("courses/course-1/podcast.json") == {"title": "Résumé"}
    download.readall.return_value = b"x" * 65
    with pytest.raises(ValueError, match="size limit"):
        storage.read_text("courses/course-1/01 - Welcome.txt", max_bytes=64)


def test_download_prefix_preserves_names(blob_service, tmp_path):
    container = blob_service.get_container_client.return_value
    container.list_blobs.return_value = [SimpleNamespace(name="course-1/01 - 100% #1.txt")]
    container.download_blob.return_value.readinto.side_effect = lambda file: file.write(b"hello")
    paths = storage.download_prefix("courses/course-1/", tmp_path)
    assert paths == [tmp_path / "01 - 100% #1.txt"]
    assert paths[0].read_bytes() == b"hello"


def test_download_uploads_do_not_overwrite_same_basename(blob_service, tmp_path):
    blob_service.get_blob_client.return_value.download_blob.return_value.readinto.side_effect = (
        lambda file: file.write(b"source")
    )
    names = ["uploads/run-1/guide.txt", "uploads/run-2/guide.txt"]
    assert storage.download_blobs(names, tmp_path) == [tmp_path / name for name in names]


@pytest.mark.parametrize("name", [
    "courses/../secret", "/uploads/run/file", "uploads//file", "uploads/run/./file",
    "uploads/run\\file", "uploads/run/\x00file", "private/file", "uploads",
])
def test_reject_invalid_blob_names(name, blob_service, tmp_path):
    with pytest.raises(ValueError):
        storage.download_blobs([name], tmp_path)
    blob_service.get_blob_client.assert_not_called()


def test_sources_must_be_uploads(blob_service, tmp_path):
    with pytest.raises(ValueError, match="uploads"):
        storage.download_blobs(["courses/course-1/podcast.json"], tmp_path)
    blob_service.get_blob_client.assert_not_called()


def test_reject_download_traversal_and_symlink(blob_service, tmp_path):
    container = blob_service.get_container_client.return_value
    container.list_blobs.return_value = [SimpleNamespace(name="course-1/../../secret")]
    with pytest.raises(ValueError):
        storage.download_prefix("courses/course-1", tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    destination = tmp_path / "download"
    destination.mkdir()
    (destination / "link").symlink_to(outside, target_is_directory=True)
    container.list_blobs.return_value = [SimpleNamespace(name="course-1/link/secret")]
    with pytest.raises(ValueError, match="escapes"):
        storage.download_prefix("courses/course-1", destination)
    container.download_blob.assert_not_called()


def test_delete_is_bounded_and_idempotent(blob_service):
    container = blob_service.get_container_client.return_value
    container.list_blobs.return_value = [SimpleNamespace(name="course-1/episode.txt")]
    blob_service.get_blob_client.return_value.delete_blob.side_effect = ResourceNotFoundError()
    storage.delete_prefix("courses/course-1/")
    container.list_blobs.assert_called_once_with(name_starts_with="course-1/")
    blob_service.get_blob_client.assert_called_once_with("courses", "course-1/episode.txt")


def test_media_properties_do_not_issue_links(blob_service):
    blob = blob_service.get_blob_client.return_value
    blob.get_blob_properties.return_value = SimpleNamespace(size=5, etag="revision")
    properties = storage.blob_properties("courses/course-1/01 - 100% #1.mp3")
    blob_service.get_blob_client.assert_called_once_with("courses", "course-1/01 - 100% #1.mp3")
    assert properties.size == 5
    assert properties.etag == "revision"
    blob.download_blob.assert_not_called()
    blob_service.get_user_delegation_key.assert_not_called()


def test_media_stream_is_bounded_to_requested_range_and_version(blob_service):
    download = blob_service.get_blob_client.return_value.download_blob
    download.return_value.chunks.return_value = iter([b"au", b"dio"])
    assert b"".join(storage.open_blob(
        "courses/course-1/episode.mp3", offset=2, length=5, etag="revision",
    )) == b"audio"
    download.assert_called_once_with(
        offset=2, length=5, max_concurrency=1,
        etag="revision", match_condition=MatchConditions.IfNotModified,
    )
    blob_service.get_user_delegation_key.assert_not_called()
