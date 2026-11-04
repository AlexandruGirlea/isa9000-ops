from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
from pathlib import Path

from pulumi.automation import (
    ConfigValue,
    LocalWorkspaceOptions,
    PulumiCommand,
    create_or_select_stack,
)
from semver import VersionInfo

parser = argparse.ArgumentParser(description="Manage only the local Foundry model resources.")
parser.add_argument("action", choices=["preview", "up"])
parser.add_argument("--location", default="eastus2")
parser.add_argument("--resource-group", required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parent
private = Path.home() / ".config" / "isa9000-ops"
data = Path.home() / ".local" / "share" / "isa9000-ops"
private.mkdir(parents=True, exist_ok=True, mode=0o700)
state = data / "pulumi-state"
state.mkdir(parents=True, exist_ok=True, mode=0o700)
passphrase = private / "pulumi-passphrase"
if not passphrase.exists():
    descriptor = os.open(passphrase, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(secrets.token_urlsafe(64))
subscription = subprocess.run(
    ["az", "account", "show", "--query", "id", "--output", "tsv"],
    capture_output=True, text=True, check=True,
).stdout.strip()
command = PulumiCommand.install(root=str(data / "cli"), version=VersionInfo.parse("3.273.0"))
stack = create_or_select_stack(
    stack_name="local", work_dir=str(root / "foundry"),
    opts=LocalWorkspaceOptions(
        pulumi_command=command, pulumi_home=str(data / "pulumi-home"),
        env_vars={
            "PULUMI_BACKEND_URL": state.as_uri(),
            "PULUMI_CONFIG_PASSPHRASE": passphrase.read_text(),
        },
    ),
)
stack.set_all_config({
    "azure-native:subscriptionId": ConfigValue(subscription),
    "azure-native:location": ConfigValue(args.location),
    "isa9000:resourceGroup": ConfigValue(args.resource_group),
})
if args.action == "preview":
    stack.preview(on_output=print)
else:
    result = stack.up(on_output=print)
    outputs = {key: value.value for key, value in result.outputs.items() if not value.secret}
    (private / "foundry.json").write_text(json.dumps(outputs, indent=2) + "\n")
    print("Local Foundry metadata saved outside the repository.")
