from __future__ import annotations

import argparse
import base64
import os
import secrets
from pathlib import Path

parser = argparse.ArgumentParser(description="Configure local Entra sign-in without cloud writes.")
parser.add_argument("--tenant")
parser.add_argument("--client")
parser.add_argument("--admin-group")
args = parser.parse_args()
path = Path(__file__).resolve().parents[1] / ".env"
lines = path.read_text().splitlines() if path.exists() else []
values = {
    line.split("=", 1)[0].strip(): line.split("=", 1)[1]
    for line in lines if "=" in line and not line.lstrip().startswith("#")
}
values.update({key: value for key, value in os.environ.items() if key.startswith("ISA9000_")})
updates = {}
for key in ("ISA9000_DJANGO_SECRET_KEY", "ISA9000_CALLBACK_SECRET"):
    if key in os.environ and not os.environ[key]:
        raise SystemExit(f"Export a nonempty {key} or unset it before configuring.")
    if not values.get(key) or values[key].startswith("local-demo-"):
        updates[key] = secrets.token_urlsafe(64)
if "ISA9000_PAYLOAD_KEY" in os.environ and not os.environ["ISA9000_PAYLOAD_KEY"]:
    raise SystemExit("Export a nonempty ISA9000_PAYLOAD_KEY or unset it before configuring.")
if not values.get("ISA9000_PAYLOAD_KEY"):
    updates["ISA9000_PAYLOAD_KEY"] = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
for key, value in (
    ("ISA9000_ENTRA_TENANT_ID", args.tenant),
    ("ISA9000_ENTRA_CLIENT_ID", args.client),
    ("ISA9000_ADMIN_GROUP_ID", args.admin_group),
):
    if value:
        updates[key] = value
if updates:
    lines = [line for line in lines if line.split("=", 1)[0].strip() not in updates]
    lines.extend(f"{key}={value}" for key, value in updates.items())
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write("\n".join(lines) + "\n")
if path.exists():
    path.chmod(0o600)
print("Local settings are ready. No Azure resources were changed.")
if not values.get("ISA9000_ENTRA_CLIENT_SECRET"):
    print("Microsoft sign-in stays closed until the Entra client credential is configured.")
