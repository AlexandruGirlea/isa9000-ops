from __future__ import annotations

import hashlib
import ipaddress
import json
import tomllib
from pathlib import Path

import pulumi
from pulumi_azure_native import (
    app,
    applicationinsights,
    authorization,
    managedidentity,
    operationalinsights,
    resources,
)

config = pulumi.Config("isa9000")
azure = pulumi.Config("azure-native")
environment = config.require("environment")
if environment not in {"dev", "test", "prod"}:
    raise ValueError("isa9000:environment must be dev, test or prod")

location = azure.require("location")
subscription = azure.require("subscriptionId")
client = authorization.get_client_config()
tenant = client.tenant_id
tags = {"project": "isa9000", "env": environment, "managedBy": "pulumi"}
suffix = hashlib.sha256(f"{subscription}/{environment}".encode()).hexdigest()[:8]
prefix = f"isa9000-{environment}"
resource_group = config.get("resourceGroup") or f"rg-{prefix}"
create_resource_group = config.get_bool("createResourceGroup")
if create_resource_group is None:
    create_resource_group = True

ingress = config.get("ingress") or "public"
if ingress == "custom-domain":
    raise ValueError("Custom domains await a staged DNS and domain-binding contract")
if ingress != "public":
    raise ValueError("Internal ingress awaits the client's network and certificate contract")
allowed_ips = config.get_object("allowedIps") or []
for cidr in allowed_ips:
    if ipaddress.ip_network(cidr).version != 4:
        raise ValueError("Container Apps allowedIps must contain IPv4 CIDRs")

image_tag = config.require("imageTag")
image = "ghcr.io/alexandrugirlea/isa9000-ops"
image = f"{image}@{image_tag}" if image_tag.startswith("sha256:") else f"{image}:{image_tag}"
admin_group = config.require_secret("adminGroupId")
alert_email = config.require("alertEmail")
group_claims = config.get("groupClaims") or "SecurityGroup"
if group_claims not in {"SecurityGroup", "ApplicationGroup"}:
    raise ValueError("isa9000:groupClaims must be SecurityGroup or ApplicationGroup")
graph_groups = config.get_bool("graphGroups") or False

auth_app = config.get("authApp") or "create"
if auth_app != "create":
    auth_app = json.loads(auth_app)
if auth_app != "create" and not isinstance(auth_app, dict):
    raise ValueError("isa9000:authApp must be create or an object with clientId and clientSecret")
if isinstance(auth_app, dict) and not {"clientId", "clientSecret"}.issubset(auth_app):
    raise ValueError("isa9000:authApp requires clientId and clientSecret")
foundry = config.get("foundry") or "create"
if foundry != "create":
    foundry = json.loads(foundry)
if foundry != "create" and not isinstance(foundry, dict):
    raise ValueError("isa9000:foundry must be create or an object with endpoint and resourceId")
if isinstance(foundry, dict):
    if not {"endpoint", "resourceId"}.issubset(foundry):
        raise ValueError("isa9000:foundry requires endpoint and resourceId")
models = config.require_object("models")
if sorted(item["role"] for item in models) != ["image", "text", "transcribe"]:
    raise ValueError("isa9000:models requires exactly one text, image and transcribe deployment")
for model in models:
    required = {"role", "name"}
    if foundry == "create":
        required |= {"model", "version", "format", "sku", "capacity"}
    if not required.issubset(model):
        raise ValueError(f"Incomplete isa9000:models entry for {model['role']}")

config_dir = Path(__file__).resolve().parent.parent / "config"
config_text = (config_dir / "isa9000.toml").read_text()
branding = tomllib.loads(config_text)
if "models" in branding:
    raise ValueError("config/isa9000.toml must not contain models; use isa9000:models")
if branding.get("branding", {}).get("logo", "") not in {"", "logo.svg"}:
    raise ValueError("The runtime branding logo must be logo.svg or empty")
config_text += "\n[models]\n" + "".join(
    f"{model['role']} = {json.dumps(model['name'])}\n" for model in models
)
config_secrets = [{"name": "isa9000-config", "value": config_text}]
config_files = [{"secret_ref": "isa9000-config", "path": "isa9000.toml"}]
if (config_dir / "logo.svg").exists():
    config_secrets.append({"name": "isa9000-logo", "value": (config_dir / "logo.svg").read_text()})
    config_files.append({"secret_ref": "isa9000-logo", "path": "logo.svg"})
elif branding.get("branding", {}).get("logo"):
    raise ValueError("config/logo.svg is required by the branding configuration")

config_volume = {"name": "config", "storage_type": "Secret", "secrets": config_files}
config_mount = {"volume_name": "config", "mount_path": "/config"}

if create_resource_group:
    group = resources.ResourceGroup(
        "resource-group", resource_group_name=resource_group, location=location, tags=tags
    )
    group_name = group.name
    group_id = group.id
else:
    group = resources.get_resource_group(resource_group_name=resource_group)
    group_name = group.name
    group_id = group.id

common = {"resource_group_name": group_name, "location": location, "tags": tags}
web_identity = managedidentity.UserAssignedIdentity(
    "web-identity",
    resource_name_=f"{prefix}-web",
    **common,
)
worker_identity = managedidentity.UserAssignedIdentity(
    "worker-identity",
    resource_name_=f"{prefix}-worker",
    **common,
)
watchdog_identity = managedidentity.UserAssignedIdentity(
    "watchdog-identity",
    resource_name_=f"{prefix}-watchdog",
    **common,
)
retention = config.get_int("logRetentionDays") or (365 if environment == "prod" else 30)
if environment == "prod" and retention < 365:
    raise ValueError("Production logRetentionDays must be at least 365")
workspace = operationalinsights.Workspace(
    "logs",
    workspace_name=f"{prefix}-logs",
    retention_in_days=retention,
    sku={"name": "PerGB2018"},
    **common,
)
insights = applicationinsights.Component(
    "insights",
    resource_name_=f"{prefix}-insights",
    application_type="web",
    kind="web",
    workspace_resource_id=workspace.id,
    retention_in_days=retention,
    sampling_percentage=100,
    disable_local_auth=True,
    **common,
)
workspace_keys = operationalinsights.get_shared_keys_output(
    resource_group_name=group_name, workspace_name=workspace.name
)
app_environment = app.ManagedEnvironment(
    "environment",
    environment_name=f"{prefix}-apps",
    app_logs_configuration={
        "destination": "log-analytics",
        "log_analytics_configuration": {
            "customer_id": workspace.customer_id,
            "shared_key": pulumi.Output.secret(workspace_keys.primary_shared_key),
        },
    },
    workload_profiles=[{"name": "Consumption", "workload_profile_type": "Consumption"}],
    **common,
)
