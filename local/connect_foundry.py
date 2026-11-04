from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
private = Path.home() / ".config" / "isa9000-ops"
metadata = json.loads((private / "foundry.json").read_text())
subprocess.run(["git", "check-ignore", "--quiet", ".env"], cwd=root, check=True)
shared = (root / "config" / "isa9000.toml").read_text()
parsed = tomllib.loads(shared)
if "models" in parsed or any(
    parsed.get(provider, {}).get("api_key") for provider in ("azure", "google")
):
    raise SystemExit("Shared branding configuration must contain no models or credentials.")
config = private / "isa9000.toml"
config.write_text(shared + "\n[models]\n" + "".join(
    f"{role} = {json.dumps(name)}\n" for role, name in metadata["models"].items()
))
config.chmod(0o644)
result = subprocess.run(
    ["az", "cognitiveservices", "account", "keys", "list", "--name", metadata["accountName"],
     "--resource-group", metadata["resourceGroup"], "--output", "json"],
    capture_output=True, text=True, check=True,
)
updates = {
    "ISA9000_AZURE_OPENAI_ENDPOINT": metadata["endpoint"],
    "ISA9000_AZURE_OPENAI_API_KEY": json.loads(result.stdout)["key1"],
    "ISA9000_CLI": "python -m worker.cli",
    "ISA9000_CONFIG_FILE": str(config),
}
path = root / ".env"
lines = [line for line in path.read_text().splitlines() if line.split("=", 1)[0] not in updates]
for key, value in updates.items():
    lines.append(key + "='" + value.replace("\\", "\\\\").replace("'", "\\'") + "'")
descriptor = os.open(path, os.O_WRONLY | os.O_TRUNC, 0o600)
with os.fdopen(descriptor, "w") as file:
    file.write("\n".join(lines) + "\n")
path.chmod(0o600)
print(
    "Real worker configured. Credentials remain in ignored .env; shared model TOML is outside git."
)
print("Restart web and worker with docker compose up --detach --no-deps web worker.")
