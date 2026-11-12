from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/deploy-stack.yml"


@pytest.mark.parametrize("status", ["Succeeded", "Failed"])
def test_workloads_roll_out_only_after_successful_migration(tmp_path, status):
    commands = tmp_path / "commands.log"
    for name in ("pulumi", "az", "python"):
        executable = tmp_path / name
        executable.write_text(
            "#!/bin/sh\n"
            'printf "%s %s\\n" "${0##*/}" "$*" >> "$COMMAND_LOG"\n'
            'case "${0##*/}:$1:$2" in\n'
            '  pulumi:stack:output) printf "%s\\n" "$3" ;;\n'
            '  az:containerapp:job)\n'
            '    case "$3" in\n'
            '      start) printf "migration-execution\\n" ;;\n'
            '      execution) printf "%s\\n" "$MIGRATION_STATUS" ;;\n'
            '    esac ;;\n'
            "esac\n"
        )
        executable.chmod(0o755)
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["deploy"]["steps"]
    start = next(i for i, step in enumerate(steps) if step.get("name") == "Prepare deployment")
    stop = next(i for i, step in enumerate(steps) if step.get("name") == "Smoke test")
    for step in steps[start:stop]:
        result = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
            cwd=tmp_path,
            env={
                **os.environ,
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "COMMAND_LOG": str(commands),
                "MIGRATION_STATUS": status,
                "STACK": "dev",
                "STACK_CONFIG": "config: {}",
                "ADMIN_GROUP_ID": "test-admin-group",
                "PULUMI_CONFIG_PASSPHRASE": "test-passphrase",
                "PULUMI_BACKEND_URL": "file:///test-state",
                "AUTH_CLIENT_SECRET": "",
                "REGISTRY": "",
                "IMAGE_TAG": "",
            },
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        if result.returncode:
            break
    log = commands.read_text().splitlines()
    updates = [line for line in log if line.startswith("pulumi up ")]
    assert "--exclude-dependents" in updates[0]
    for resource in (
        "ContainerApp::web", "Job::worker", "ContainerApp::worker", "Job::watchdog",
        "ContainerAppsAuthConfig::easyauth", "ScheduledQueryRule::worker-executions",
        "RoleAssignment::web-secret-database", "RoleAssignment::watchdog-secret-database",
    ):
        assert resource in updates[0]
    assert log.index(updates[0]) < next(
        i for i, line in enumerate(log) if line.startswith("az containerapp job start ")
    )
    if status == "Succeeded":
        assert result.returncode == 0, result.stderr
        assert len(updates) == 2
        assert "--exclude" not in updates[1]
        assert log.index(updates[1]) > next(
            i for i, line in enumerate(log) if line.startswith("az containerapp job execution ")
        )
    else:
        assert result.returncode != 0
        assert len(updates) == 1


@pytest.mark.parametrize(
    "legacy_exists, active_count, query_failure, allowed",
    [
        (False, "0", "", True),
        (True, "0", "", True),
        (True, "1", "", False),
        (True, "", "", False),
        (True, "unexpected", "", False),
        (False, "0", "list", False),
        (True, "0", "execution", False),
    ],
)
def test_legacy_worker_must_be_idle_before_replacement(
    tmp_path, legacy_exists, active_count, query_failure, allowed,
):
    commands = tmp_path / "commands.log"
    for name in ("pulumi", "az", "python"):
        executable = tmp_path / name
        executable.write_text(
            "#!/bin/sh\n"
            'printf "%s %s\\n" "${0##*/}" "$*" >> "$COMMAND_LOG"\n'
            'case "${0##*/}:$1:$2:$3" in\n'
            '  pulumi:stack:output:resourceGroup) printf "test-group\\n" ;;\n'
            '  az:containerapp:job:list)\n'
            '    [ "$QUERY_FAILURE" != list ] || exit 1\n'
            '    [ "$LEGACY_EXISTS" != true ] || printf "isa9000-prod-worker\\n" ;;\n'
            '  az:containerapp:job:execution)\n'
            '    [ "$QUERY_FAILURE" != execution ] || exit 1\n'
            '    printf "%s\\n" "$ACTIVE_COUNT" ;;\n'
            "esac\n"
        )
        executable.chmod(0o755)
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["deploy"]["steps"]
    rollout = next(step for step in steps if step.get("name") == "Roll out application")
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", rollout["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "COMMAND_LOG": str(commands),
            "STACK": "prod",
            "LEGACY_EXISTS": str(legacy_exists).lower(),
            "ACTIVE_COUNT": active_count,
            "QUERY_FAILURE": query_failure,
        },
        text=True, capture_output=True, check=False, timeout=10,
    )
    log = commands.read_text().splitlines()
    assert (result.returncode == 0) is allowed, result.stderr
    updates = [line for line in log if line.startswith("pulumi up ")]
    assert len(updates) == int(allowed)
    assert not any("job stop" in line or "job delete" in line for line in log)
    queries = [line for line in log if line.startswith("az containerapp job execution list ")]
    if queries:
        assert "--name isa9000-prod-worker" in queries[0]
        for terminal in ("Succeeded", "Failed", "Stopped"):
            assert f"properties.status!='{terminal}'" in queries[0]
        if allowed:
            assert log.index(queries[0]) < log.index(updates[0])
