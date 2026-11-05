from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARGUMENTS = ("sub-id", "eastus2", "state-group", "isa9000state", "client/isa9000-ops")
IMMUTABLE = "repo:client@15/isa9000-ops@14"


def run_bootstrap(tmp_path, gh_output, **environment):
    log = tmp_path / "commands.log"
    for name, body in (
        ("az", f'printf \'az %s\\n\' "$*" >> "{log}"\nprintf \'id\\n\'\n'),
        ("gh", f'printf \'gh %s\\n\' "$*" >> "{log}"\nprintf \'%s\\n\' \'{gh_output}\'\n'),
    ):
        tool = tmp_path / name
        tool.write_text(f"#!/bin/sh\n{body}")
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    result = subprocess.run(
        ["bash", str(ROOT / "bootstrap.sh"), *ARGUMENTS],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", **environment},
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    return result, log.read_text() if log.exists() else ""


def test_federation_uses_the_subject_github_issues(tmp_path):
    result, commands = run_bootstrap(tmp_path, IMMUTABLE)
    assert result.returncode == 0, result.stderr
    assert "repos/client/isa9000-ops/actions/oidc/customization/sub" in commands
    for environment in ("dev", "test", "prod", "preview"):
        assert f"--subject {IMMUTABLE}:environment:{environment} " in commands
    assert "--subject repo:client/isa9000-ops:environment" not in commands


def test_explicit_prefix_needs_no_github_access(tmp_path):
    result, commands = run_bootstrap(
        tmp_path, "", GITHUB_OIDC_SUBJECT_PREFIX="repo:client/isa9000-ops",
    )
    assert result.returncode == 0, result.stderr
    assert "gh api" not in commands
    assert "--subject repo:client/isa9000-ops:environment:dev " in commands


def test_unusable_subject_stops_before_any_azure_change(tmp_path):
    result, commands = run_bootstrap(tmp_path, "")
    assert result.returncode == 1
    assert "GITHUB_OIDC_SUBJECT_PREFIX" in result.stderr
    assert "az " not in commands
