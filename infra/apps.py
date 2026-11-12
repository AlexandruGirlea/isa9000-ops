from __future__ import annotations

from pathlib import Path

import pulumi
import pulumi_azuread as azuread
from pulumi_azure_native import app, keyvault

import core as s
import data
import edge

if s.auth_app == "create":
    registration = azuread.Application(
        "auth-app",
        display_name=s.prefix,
        sign_in_audience="AzureADMyOrg",
        group_membership_claims=[s.group_claims],
        # What sign-in requests with graphGroups, declared so IT can grant all of it beforehand.
        required_resource_accesses=[{
            "resource_app_id": "00000003-0000-0000-c000-000000000000",  # Microsoft Graph
            "resource_accesses": [
                {"id": "37f7f235-527c-4136-accd-4a02d197296e", "type": "Scope"},  # openid
                {"id": "14dad69e-099b-42c9-810b-d002981feec1", "type": "Scope"},  # profile
                {"id": "64a6cdd6-aab1-4aaf-94b8-3cc8405e90d0", "type": "Scope"},  # email
                {"id": "e1fe6dd8-ba31-4d61-89e7-88639da4683d", "type": "Scope"},  # User.Read
            ],
        }],
        owners=[s.client.object_id],
        web={
            "redirect_uris": [
                pulumi.Output.concat("https://", edge.azure_host, "/.auth/login/aad/callback"),
            ],
            "implicit_grant": {"id_token_issuance_enabled": True},
        },
        tags=[f"{key}={value}" for key, value in s.tags.items()],
    )
    azuread.ServicePrincipal(
        "auth-service-principal",
        client_id=registration.client_id,
        app_role_assignment_required=False,
        owners=[s.client.object_id],
        tags=[f"{key}={value}" for key, value in s.tags.items()],
    )
    password = azuread.ApplicationPassword("auth-password", application_id=registration.id)
    auth_client_id = registration.client_id
    auth_secret = password.value
else:
    auth_client_id = s.auth_app["clientId"]
    auth_secret = s.config.require_secret_object("authApp").apply(
        lambda value: value["clientSecret"]
    )


def identity(resource):
    return {
        "type": "UserAssigned",
        "user_assigned_identities": resource.id.apply(lambda rid: {rid: {}}),
    }


def registries(resource):
    return [{"server": s.registry_server, "identity": resource.id}] if s.registry_server else None


def variables(values, secrets):
    return [{"name": key, "value": value} for key, value in values.items()] + [
        {"name": key, "secret_ref": value} for key, value in secrets.items()
    ]


telemetry_env = {"APPLICATIONINSIGHTS_CONNECTION_STRING": s.insights.connection_string}
shared_env = {
    **telemetry_env,
    "ISA9000_STORAGE_ACCOUNT": data.account.name,
    "ISA9000_SERVICEBUS_NAMESPACE": pulumi.Output.concat(data.bus.name, ".servicebus.windows.net"),
    "ISA9000_QUEUE": "tasks",
    "ISA9000_CONFIG": "/config/isa9000.toml",
}
vault_secrets = {
    name: keyvault.Secret(
        f"secret-{name}",
        resource_group_name=data.group_name,
        vault_name=data.vault.name,
        secret_name=name,
        properties={"value": pulumi.Output.secret(value)},
        tags=s.tags,
    )
    for name, value in {
        "database": data.database_admin_url,
        "database-runtime": data.database_url,
        "django-key": data.django_secret.result,
        "callback": data.callback_secret.result,
        "payload-key": data.payload_key.base64,
        "admin-group": s.admin_group,
        "auth-client-secret": auth_secret,
    }.items()
}
credential_refs = {}
for workload, resource, names in (
    (
        "web",
        data.web_identity,
        (
            "database-runtime", "django-key", "callback", "payload-key", "admin-group",
            "auth-client-secret",
        ),
    ),
    ("worker", data.worker_identity, ("callback", "payload-key")),
    ("watchdog", data.watchdog_identity, ("database-runtime", "django-key", "admin-group")),
    ("migrate", data.migrate_identity, ("database", "database-runtime", "django-key")),
):
    credential_refs[workload] = []
    for name in names:
        secret = vault_secrets[name]
        data.grant(
            f"{workload}-secret-{name}", resource, secret.id,
            "4633458b-17de-408a-b874-0445c86b69e6",
        )
        credential_refs[workload].append({
            "name": name,
            "key_vault_url": secret.properties.apply(lambda properties: properties.secret_uri),
            "identity": resource.id,
        })
web_secrets = [*s.config_secrets, *credential_refs["web"]]
web_env = variables(
    {
        **shared_env,
        "OTEL_SERVICE_NAME": "isa9000-web",
        "AZURE_CLIENT_ID": data.web_identity.client_id,
        "ISA9000_AUTH": "easyauth",
        "ISA9000_ENTRA_TENANT_ID": s.tenant,
        "ISA9000_ENTRA_CLIENT_ID": auth_client_id,
        "ISA9000_GRAPH_GROUPS": str(s.graph_groups).lower(),
        "ISA9000_ALLOWED_HOSTS": edge.azure_host,
        "ISA9000_CALLBACK_BASE_URL": pulumi.Output.concat("https://", edge.azure_host),
        "ISA9000_MIGRATE_ON_START": "false",
        "ISA9000_DAILY_RUNS_PER_USER": str(s.config.get_int("dailyRunsPerUser") or 100),
        "ISA9000_MAX_UPLOAD_MB": str(s.config.get_int("maxUploadMb") or 10),
        "ISA9000_MAX_FILES": str(s.config.get_int("maxFiles") or 5),
    },
    {
        "DATABASE_URL": "database-runtime",
        "ISA9000_DJANGO_SECRET_KEY": "django-key",
        "ISA9000_CALLBACK_SECRET": "callback",
        "ISA9000_PAYLOAD_KEY": "payload-key",
        "ISA9000_ADMIN_GROUP_ID": "admin-group",
    },
)
minimum = s.config.get_int("webMinReplicas")
if minimum is None:
    minimum = 1
maximum = s.config.get_int("webMaxReplicas") or (5 if s.environment == "prod" else 3)
if minimum < 1 or maximum < minimum:
    raise ValueError("webMinReplicas must be at least 1 and webMaxReplicas at least webMinReplicas")
web = app.ContainerApp(
    "web",
    container_app_name=edge.web_name,
    environment_id=s.app_environment.id,
    identity=identity(data.web_identity),
    workload_profile_name="Consumption",
    configuration={
        "active_revisions_mode": "Single",
        "secrets": web_secrets,
        "registries": registries(data.web_identity),
        "ingress": {
            "external": True,
            "target_port": 8000,
            "transport": "auto",
            "allow_insecure": False,
            "ip_security_restrictions": edge.restrictions,
        },
    },
    template={
        "containers": [
            {
                "name": "web",
                "image": s.image,
                "args": ["gunicorn", "-c", "gunicorn.conf.py", "core.wsgi"],
                "resources": (
                    {"cpu": 2, "memory": "4Gi"} if s.environment == "prod"
                    else {"cpu": 0.5, "memory": "1Gi"}
                ),
                "env": web_env,
                "volume_mounts": [s.config_mount],
                "probes": [
                    {
                        "type": probe,
                        "initial_delay_seconds": 10,
                        "period_seconds": 10,
                        "http_get": {
                            "path": "/healthz",
                            "port": 8000,
                            "http_headers": [
                                {"name": "Host", "value": edge.azure_host},
                                {"name": "X-Forwarded-Proto", "value": "https"},
                            ],
                        },
                    }
                    for probe in ("Liveness", "Readiness")
                ],
            }
        ],
        "volumes": [s.config_volume],
        "scale": {
            "min_replicas": minimum,
            "max_replicas": maximum,
            "rules": [{"name": "http", "http": {"metadata": {"concurrentRequests": "50"}}}],
        },
    },
    opts=pulumi.ResourceOptions(
        depends_on=[
            *data.permissions,
            *data.database_access,
            data.database_ready,
        ]
    ),
    **data.common,
)
auth = app.ContainerAppsAuthConfig(
    "easyauth",
    resource_group_name=data.group_name,
    container_app_name=web.name,
    auth_config_name="current",
    platform={"enabled": True},
    login={
        "cookie_expiration": {"convention": "FixedTime", "time_to_expiration": "08:00:00"},
        "token_store": {
            "enabled": True,
            "token_refresh_extension_hours": 0,
            "azure_blob_storage": {
                "blob_container_uri": pulumi.Output.concat(
                    "https://", data.account.name, ".blob.core.windows.net/auth-tokens"
                ),
                "managed_identity_resource_id": data.web_identity.id,
            },
        },
    },
    global_validation={
        "unauthenticated_client_action": "RedirectToLoginPage",
        "redirect_to_provider": "azureactivedirectory",
        "excluded_paths": ["/", "/static/*", "/healthz", "/internal/*"],
    },
    identity_providers={
        "azure_active_directory": {
            "enabled": True,
            "registration": {
                "client_id": auth_client_id,
                "client_secret_setting_name": "auth-client-secret",
                "open_id_issuer": f"https://login.microsoftonline.com/{s.tenant}/v2.0",
            },
            "validation": {"allowed_audiences": [auth_client_id]},
            "login": (
                {"login_parameters": ["scope=openid profile email User.Read"]}
                if s.graph_groups else None
            ),
        }
    },
    http_settings={"require_https": True, "forward_proxy": {"convention": "NoProxy"}},
)

worker_secrets = [*s.config_secrets, *credential_refs["worker"]]
worker_secret_env = {"ISA9000_CALLBACK_SECRET": "callback", "ISA9000_PAYLOAD_KEY": "payload-key"}
worker_template = {
    "containers": [
        {
            "name": "worker",
            "image": s.image,
            "args": ["python", "-m", "worker"],
            "resources": (
                {"cpu": 2, "memory": "4Gi"} if s.environment == "prod"
                else {"cpu": 1, "memory": "2Gi"}
            ),
            "env": variables(
                {
                    **shared_env,
                    "OTEL_SERVICE_NAME": "isa9000-worker",
                    "AZURE_CLIENT_ID": data.worker_identity.client_id,
                    "ISA9000_AZURE_OPENAI_ENDPOINT": data.foundry_endpoint,
                    "ISA9000_CALLBACK_BASE_URL": pulumi.Output.concat(
                        "https://", edge.azure_host
                    ),
                },
                worker_secret_env,
            ),
            "volume_mounts": [s.config_mount],
        }
    ],
    "volumes": [s.config_volume],
}
worker_common = {
    "environment_id": s.app_environment.id,
    "identity": identity(data.worker_identity),
    "workload_profile_name": "Consumption",
    "opts": pulumi.ResourceOptions(depends_on=[*data.permissions, auth]),
    **data.common,
}
worker_rule = {
    "type": "azure-servicebus",
    "identity": data.worker_identity.id,
    "metadata": {
        "namespace": data.bus.name,
        "queueName": "tasks",
        "messageCount": "1",
    },
}
worker_maximum = s.config.get_int("workerMaxExecutions")
if worker_maximum is None:
    worker_maximum = 5
if worker_maximum < 1:
    raise ValueError("workerMaxExecutions must be at least 1")
worker_polling = s.config.get_int("workerPollingSeconds") or 10
worker_template["containers"][0]["args"] = [
    "python", "-c", Path(__file__).with_name("worker_service.py").read_text(),
]
worker = app.ContainerApp(
    "worker",
    container_app_name=f"{s.prefix}-worker",
    configuration={
        "active_revisions_mode": "Single",
        "secrets": worker_secrets,
        "registries": registries(data.worker_identity),
    },
    template={
        **worker_template,
        "termination_grace_period_seconds": 600,
        "scale": {
            "min_replicas": 1,
            "max_replicas": worker_maximum,
            "polling_interval": worker_polling,
            "rules": [{"name": "tasks", "custom": worker_rule}],
        },
    },
    **worker_common,
)
watchdog = app.Job(
    "watchdog",
    job_name=f"{s.prefix}-watchdog",
    environment_id=s.app_environment.id,
    identity=identity(data.watchdog_identity),
    workload_profile_name="Consumption",
    configuration={
        "trigger_type": "Schedule",
        "replica_timeout": 240,
        "replica_retry_limit": 0,
        "schedule_trigger_config": {
            "cron_expression": "*/5 * * * *",
            "parallelism": 1,
            "replica_completion_count": 1,
        },
        "secrets": credential_refs["watchdog"],
        "registries": registries(data.watchdog_identity),
    },
    template={
        "containers": [
            {
                "name": "watchdog",
                "image": s.image,
                "args": ["python", "manage.py", "expire_runs"],
                "resources": {"cpu": 0.25, "memory": "0.5Gi"},
                "env": variables(
                    {
                        **telemetry_env,
                        "OTEL_SERVICE_NAME": "isa9000-watchdog",
                        "AZURE_CLIENT_ID": data.watchdog_identity.client_id,
                    },
                    {
                        "DATABASE_URL": "database-runtime",
                        "ISA9000_DJANGO_SECRET_KEY": "django-key",
                        "ISA9000_ADMIN_GROUP_ID": "admin-group",
                    },
                ),
            }
        ]
    },
    opts=pulumi.ResourceOptions(depends_on=data.permissions),
    **data.common,
)
migrate = app.Job(
    "migrate",
    job_name=f"{s.prefix}-migrate",
    environment_id=s.app_environment.id,
    identity=identity(data.migrate_identity),
    workload_profile_name="Consumption",
    configuration={
        "trigger_type": "Manual",
        "replica_timeout": 900,
        "replica_retry_limit": 0,
        "manual_trigger_config": {"parallelism": 1, "replica_completion_count": 1},
        "secrets": credential_refs["migrate"],
        "registries": registries(data.migrate_identity),
    },
    template={
        "containers": [
            {
                "name": "migrate",
                "image": s.image,
                "args": ["python", "manage.py", "migrate_database"],
                "resources": {"cpu": 0.5, "memory": "1Gi"},
                "env": variables(
                    {},
                    {
                        "DATABASE_URL": "database",
                        "ISA9000_RUNTIME_DATABASE_URL": "database-runtime",
                        "ISA9000_DJANGO_SECRET_KEY": "django-key",
                    },
                ),
            }
        ]
    },
    opts=pulumi.ResourceOptions(depends_on=data.permissions),
    **data.common,
)
