from __future__ import annotations

import json
import os
import subprocess

import pytest
from isa9000.podcast import load_manifest
from isa9000.presentation_website import _collect, _page


@pytest.mark.parametrize("code", [0, 1, 3])
def test_stub_outcomes(code, stub_cli, branding_config, tmp_path):
    result = subprocess.run(
        [*stub_cli, "--config", str(branding_config), "--progress", "json", "create",
         "--request", "A local demo", "--source", "https://example.com/guide",
         "--audio-kb", str(tmp_path / "kb")],
        env={**os.environ, "ISA9000_STUB_EXIT": str(code)},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == code, result.stderr
    steps = [json.loads(line) for line in result.stdout.splitlines()]
    assert all("stage" in step and "message" in step for step in steps)
    if code == 1:
        assert steps[-1]["stage"] == "error"
        assert "\n" in steps[-1]["message"]
        assert not list(tmp_path.rglob("podcast.json"))
        return
    folder = tmp_path / "kb" / "Demo course"
    manifest = load_manifest(folder)
    assert len(manifest["episodes"]) == 2
    assert (folder / manifest["cover"]).read_bytes().startswith(b"\x89PNG")
    assert manifest["review"]["approved"] == (code == 0)
    if code == 3:
        assert all(episode["audio"] is None for episode in manifest["episodes"])
        result = subprocess.run(
            [*stub_cli, "--config", str(branding_config), "--progress", "json", "record",
             str(folder)], env={**os.environ, "ISA9000_STUB_EXIT": "0"},
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr
        manifest = load_manifest(folder)
    for episode in manifest["episodes"]:
        assert episode["audio"] == episode["transcript"].replace(".txt", ".mp3")
        assert (folder / episode["audio"]).stat().st_size > 1000
    entries = _collect(tmp_path / "kb", tmp_path / "site")
    page = _page(entries[0], tmp_path / "site")
    assert len(page["episodes"]) == 2
    assert all("local demonstration" in episode["text"] for episode in page["episodes"])
