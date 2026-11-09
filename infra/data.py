from __future__ import annotations

from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

import pulumi
import pulumi_random as random
from pulumi_azure_native import (
    authorization,
    cognitiveservices,
    dbforpostgresql,
    keyvault,
    monitor,
    servicebus,
    storage,
)

import core as s
from core import common, group_name, watchdog_identity, web_identity, worker_identity

permissions = []


def grant(name, identity, scope, role):
    assignment = authorization.RoleAssignment(
        name,
        role_assignment_name=pulumi.Output.all(scope, identity.principal_id).apply(
            lambda values: str(uuid5(NAMESPACE_URL, f"{values[0]}/{values[1]}/{role}"))
        ),
        principal_id=identity.principal_id,
        principal_type="ServicePrincipal",
        role_definition_id=f"/subscriptions/{s.subscription}/providers/Microsoft.Authorization/"
        f"roleDefinitions/{role}",
        scope=scope,
    )
    permissions.append(assignment)


vault = keyvault.Vault(
    "vault",
    vault_name=f"{s.prefix}-{s.suffix}",
    properties={
        "tenant_id": s.tenant,
        "sku": {"family": "A", "name": "standard"},
        "enable_rbac_authorization": True,
        "enable_soft_delete": True,
        "enable_purge_protection": True,
        "soft_delete_retention_in_days": 90,
    },
    opts=pulumi.ResourceOptions(protect=s.environment == "prod"),
    **common,
)
monitor.DiagnosticSetting(
    "vault-audit",
    resource_uri=vault.id,
    workspace_id=s.workspace.id,
    logs=[{"category": "AuditEvent", "enabled": True}],
)

account = storage.StorageAccount(
    "storage",
    account_name=f"isa9000{s.environment}{s.suffix}",
    kind="StorageV2",
    sku={"name": "Standard_GZRS" if s.environment == "prod" else "Standard_LRS"},
    allow_blob_public_access=False,
    allow_shared_key_access=False,
    enable_https_traffic_only=True,
    minimum_tls_version="TLS1_2",
    opts=pulumi.ResourceOptions(protect=s.environment == "prod"),
    **common,
)
storage.BlobServiceProperties(
    "blob-retention",
    resource_group_name=group_name,
    account_name=account.name,
    blob_services_name="default",
    delete_retention_policy={"enabled": True, "days": 30 if s.environment == "prod" else 7},
    container_delete_retention_policy={
        "enabled": True, "days": 30 if s.environment == "prod" else 7
    },
    is_versioning_enabled=s.environment == "prod",
)
containers = {}
for container in ("courses", "uploads", "auth-tokens"):
    containers[container] = storage.BlobContainer(
        container,
        resource_group_name=group_name,
        account_name=account.name,
        container_name=container,
        public_access="None",
    )
storage.ManagementPolicy(
    "upload-expiry",
    resource_group_name=group_name,
    account_name=account.name,
    management_policy_name="default",
    policy={
        "rules": [
            {
                "name": "expire-uploads",
                "type": "Lifecycle",
                "enabled": True,
                "definition": {
                    "filters": {"blob_types": ["blockBlob"], "prefix_match": ["uploads/"]},
                    "actions": {
                        "base_blob": {"delete": {"days_after_modification_greater_than": 7}}
                    },
                },
            },
            {
                "name": "expire-auth-tokens",
                "type": "Lifecycle",
                "enabled": True,
                "definition": {
                    "filters": {"blob_types": ["blockBlob"], "prefix_match": ["auth-tokens/"]},
                    "actions": {
                        "base_blob": {"delete": {"days_after_modification_greater_than": 1}}
                    },
                },
            },
            {
                "name": "expire-old-versions",
                "type": "Lifecycle",
                "enabled": s.environment == "prod",
                "definition": {
                    "filters": {"blob_types": ["blockBlob"]},
                    "actions": {
                        "version": {"delete": {"days_after_creation_greater_than": 30}}
                    },
                },
            },
        ]
    },
)
for name, container in containers.items():
    grant(f"web-{name}", web_identity, container.id, "ba92f5b4-2d11-453d-a403-e96b0029c9fe")
grant(
    "worker-courses", worker_identity, containers["courses"].id,
    "ba92f5b4-2d11-453d-a403-e96b0029c9fe",
)
grant(
    "worker-uploads", worker_identity, containers["uploads"].id,
    "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1",
)
for name, identity in (
    ("web", web_identity), ("worker", worker_identity), ("watchdog", watchdog_identity)
):
    grant(f"{name}-telemetry", identity, s.insights.id, "3913510d-42f4-4e42-8a64-420c390055eb")
    if s.registry:
        grant(f"{name}-registry", identity, s.registry, "7f951dda-4ed3-4680-a7ca-43fe172d538d")

bus = servicebus.Namespace(
    "servicebus",
    namespace_name=f"{s.prefix}-{s.suffix}",
    sku={
        "name": "Standard" if s.environment == "prod" else "Basic",
        "tier": "Standard" if s.environment == "prod" else "Basic",
    },
    public_network_access="Enabled",
    disable_local_auth=True,
    minimum_tls_version="1.2",
    **common,
)
queue = servicebus.Queue(
    "tasks",
    resource_group_name=group_name,
    namespace_name=bus.name,
    queue_name="tasks",
    lock_duration="PT1M",
    max_delivery_count=3,
)
grant("web-send", web_identity, queue.id, "69a216fc-b8fb-44d8-bc22-1f3c2cd27a39")
grant("worker-receive", worker_identity, queue.id, "4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0")

database_password = random.RandomPassword("postgres-password", length=40, special=False)
database = dbforpostgresql.Server(
    "postgres",
    server_name=f"{s.prefix}-{s.suffix}",
    administrator_login="isa9000",
    administrator_login_password=database_password.result,
    version="16",
    sku={
        "name": s.config.get("postgresSku") or (
            "Standard_D2ds_v5" if s.environment == "prod" else "Standard_B1ms"
        ),
        "tier": s.config.get("postgresTier") or (
            "GeneralPurpose" if s.environment == "prod" else "Burstable"
        ),
    },
    storage={
        "storage_size_gb": 128 if s.environment == "prod" else 32,
        "auto_grow": "Enabled" if s.environment == "prod" else "Disabled",
    },
    high_availability={"mode": "Disabled"},
    backup={
        "backup_retention_days": 35 if s.environment == "prod" else 7,
        "geo_redundant_backup": "Enabled" if s.environment == "prod" else "Disabled",
    },
    network={"public_network_access": "Enabled"},
    auth_config={"password_auth": "Enabled", "active_directory_auth": "Disabled"},
    opts=pulumi.ResourceOptions(protect=s.environment == "prod"),
    **common,
)
database_access = dbforpostgresql.FirewallRule(
    "postgres-azure-services",
    resource_group_name=group_name,
    server_name=database.name,
    firewall_rule_name="AllowAzureServices",
    start_ip_address="0.0.0.0",
    end_ip_address="0.0.0.0",
)
database_schema = dbforpostgresql.Database(
    "database",
    resource_group_name=group_name,
    server_name=database.name,
    database_name="isa9000",
    charset="UTF8",
    opts=pulumi.ResourceOptions(depends_on=[database_access]),
)
# Azure rejects concurrent changes to one server with ServerIsBusy.
# Azure rejects the provider's reset on delete, so keep parameters; they go with the server.
database_ready = database_schema
for parameter, value in (
    ("require_secure_transport", "ON"),
    ("ssl_min_protocol_version", "TLSv1.2"),
):
    database_ready = dbforpostgresql.Configuration(
        parameter,
        resource_group_name=group_name,
        server_name=database.name,
        configuration_name=parameter,
        value=value,
        source="user-override",
        opts=pulumi.ResourceOptions(depends_on=[database_ready], retain_on_delete=True),
    )
database_url = pulumi.Output.all(
    database_password.result, database.fully_qualified_domain_name
).apply(
    lambda values: (
        f"postgresql://isa9000:{quote(values[0], safe='')}@{values[1]}:5432/isa9000"
        "?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt"
    )
)
callback_secret = random.RandomPassword("callback-secret", length=64, special=False)
django_secret = random.RandomPassword("django-secret", length=64, special=False)
payload_key = random.RandomBytes("payload-key", length=32)

if s.foundry == "create":
    foundry = cognitiveservices.Account(
        "foundry",
        account_name=f"{s.prefix}-{s.suffix}",
        kind="AIServices",
        sku={"name": "S0"},
        properties={
            "custom_sub_domain_name": f"{s.prefix}-{s.suffix}",
            "disable_local_auth": True,
        },
        **common,
    )
    for model in s.models:
        cognitiveservices.Deployment(
            f"model-{model['role']}",
            resource_group_name=group_name,
            account_name=foundry.name,
            deployment_name=model["name"],
            tags=s.tags,
            sku={"name": model["sku"], "capacity": model["capacity"]},
            properties={
                "model": {
                    "name": model["model"],
                    "version": model["version"],
                    "format": model["format"],
                },
            },
        )
    foundry_endpoint = foundry.properties.apply(lambda properties: properties.endpoint)
    foundry_id = foundry.id
else:
    foundry_endpoint = s.foundry["endpoint"]
    foundry_id = s.foundry["resourceId"]
# An account outside the application group belongs to its owner, who grants the worker access:
# deployment identities can assign roles only inside their own resource group.
application_scope = f"/subscriptions/{s.subscription}/resourcegroups/{s.resource_group}/"
if s.foundry == "create" or foundry_id.lower().startswith(application_scope.lower()):
    grant("worker-foundry", worker_identity, foundry_id, "a97b65f3-24c7-4388-baec-2e87135dc908")
