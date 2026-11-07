from __future__ import annotations

import importlib
import inspect
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

RUNNER = Path(__file__).with_name("run_stack.py")


def run_stack(environment, overrides=None):
    result = subprocess.run(
        [sys.executable, str(RUNNER), environment, json.dumps(overrides or {})],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("environment", ["dev", "test", "prod"])
def test_stack_contract(environment):
    resources = run_stack(environment)["resources"]
    production = environment == "prod"
    tags = {"project": "isa9000", "env": environment, "managedBy": "pulumi"}
    for resource in resources.values():
        if resource["type"].startswith("azure-native:"):
            _, namespace, name = resource["type"].split(":")
            args = getattr(
                importlib.import_module(f"pulumi_azure_native.{namespace}"), f"{name}Args"
            )
            if "tags" in inspect.signature(args).parameters:
                assert resource["inputs"]["tags"] == tags
    storage = resources["storage"]["inputs"]
    assert storage["sku"]["name"] == ("Standard_GZRS" if production else "Standard_LRS")
    assert storage["allowSharedKeyAccess"] is False
    assert storage["allowBlobPublicAccess"] is False
    assert storage["enableHttpsTrafficOnly"] is True
    retention = resources["blob-retention"]["inputs"]
    assert retention["deleteRetentionPolicy"]["days"] == (30 if production else 7)
    assert retention["isVersioningEnabled"] is production
    bus = resources["servicebus"]["inputs"]
    assert bus["disableLocalAuth"] is True
    assert bus["minimumTlsVersion"] == "1.2"
    postgres = resources["postgres"]["inputs"]
    assert postgres["sku"]["tier"] == ("GeneralPurpose" if production else "Burstable")
    assert postgres["backup"]["geoRedundantBackup"] == ("Enabled" if production else "Disabled")
    assert resources["require_secure_transport"]["inputs"]["value"] == "ON"
    assert resources["environment"]["inputs"]["zoneRedundant"] is production
    assert ("apps-subnet" in resources) is production
    vault = resources["vault"]["inputs"]["properties"]
    assert vault["enableRbacAuthorization"] is True
    assert vault["enablePurgeProtection"] is True

    web = resources["web"]["inputs"]
    worker = resources["worker"]["inputs"]
    watchdog = resources["watchdog"]["inputs"]
    assert web["template"]["scale"]["minReplicas"] == (1 if production else 0)
    expected_secrets = {
        "web": {"database", "django-key", "callback", "admin-group", "auth-client-secret",
                "payload-key"},
        "worker": {"callback", "payload-key"},
        "watchdog": {"database", "django-key", "admin-group"},
    }
    for name, service in (("web", web), ("worker", worker), ("watchdog", watchdog)):
        credentials = {
            item["name"]: item for item in service["configuration"]["secrets"]
            if not item["name"].startswith("isa9000-")
        }
        assert credentials.keys() == expected_secrets[name]
        for key, secret in credentials.items():
            assert "value" not in secret
            assert secret["identity"] == f"/mock/{name}-identity"
            grant = resources[f"{name}-secret-{key}"]["inputs"]
            assert grant["scope"] == f"/mock/secret-{key}"
            assert grant["principalId"] == f"{name}-identity-principal"
    config = next(
        secret["value"] for secret in worker["configuration"]["secrets"]
        if secret["name"] == "isa9000-config"
    )
    assert tomllib.loads(config)["models"] == {
        "text": "gpt-6.1-sol",
        "image": "gpt-image-2.5-sunburst",
        "transcribe": "gpt-transcribe",
    }
    web_vars = {item["name"]: item for item in web["template"]["containers"][0]["env"]}
    worker_vars = {item["name"]: item for item in worker["template"]["containers"][0]["env"]}
    assert web_vars["ISA9000_AUTH"]["value"] == "easyauth"
    assert web_vars["DATABASE_URL"]["secretRef"] == "database"
    assert "DATABASE_URL" not in worker_vars
    assert "ISA9000_AZURE_OPENAI_API_KEY" not in worker_vars
    assert all(
        "CONNECTION_STRING" not in name or name == "APPLICATIONINSIGHTS_CONNECTION_STRING"
        for name in web_vars | worker_vars
    )
    assert web["configuration"]["ingress"]["allowInsecure"] is False
    assert worker["configuration"]["replicaTimeout"] == 7200
    assert worker["configuration"]["replicaRetryLimit"] == 0
    assert worker["configuration"]["eventTriggerConfig"]["parallelism"] == 1
    assert resources["easyauth"]["inputs"]["globalValidation"]["excludedPaths"] == [
        "/",
        "/static/*",
        "/healthz",
        "/internal/*",
    ]
    for name in (
        "dead-letter-alert", "failed-runs", "server-errors", "denied-access", "worker-executions",
        "budget",
    ):
        assert name in resources


def test_created_registration_only_allows_the_sign_in_callback():
    uris = run_stack("dev")["resources"]["auth-app"]["inputs"]["web"]["redirectUris"]
    host = "https://isa9000-dev-web.mock.eastus2.azurecontainerapps.io"
    assert uris == [f"{host}/.auth/login/aad/callback"]


def test_metric_alert_uses_azure_serialized_criteria():
    criteria = run_stack("dev")["resources"]["dead-letter-alert"]["inputs"]["criteria"]
    assert criteria["odataType"] == "Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria"
    assert criteria["allOf"][0]["criterionType"] == "StaticThresholdCriterion"
    assert criteria["allOf"][0]["metricName"] == "DeadletteredMessages"


def test_postgres_changes_run_one_at_a_time_before_the_web_app():
    dependencies = run_stack("dev")["dependencies"]
    assert dependencies["database"] == ["postgres-azure-services"]
    assert dependencies["require_secure_transport"] == ["database"]
    assert dependencies["ssl_min_protocol_version"] == ["require_secure_transport"]
    assert "ssl_min_protocol_version" in dependencies["web"]


def test_client_reuses_foundry_auth_and_resource_group():
    result = run_stack(
        "dev",
        {
            "isa9000:createResourceGroup": False,
            "isa9000:resourceGroup": "existing-client-group",
            "isa9000:foundry": {
                "endpoint": "https://client.cognitiveservices.azure.com/",
                "resourceId": "/subscriptions/client/resourceGroups/client/providers/"
                "Microsoft.CognitiveServices/accounts/client",
            },
            "isa9000:models": [
                {"role": "text", "name": "gpt-6.1-sol"},
                {"role": "image", "name": "gpt-image-2.5-sunburst"},
                {"role": "transcribe", "name": "gpt-transcribe"},
            ],
            "isa9000:authApp": {
                "clientId": "provided-client",
                "clientSecret": "mock-provided-secret",
            },
        },
    )
    resources = result["resources"]
    assert not {"foundry", "auth-app", "resource-group", "model-text"} & resources.keys()
    assert "worker-foundry" not in resources
    assert "azure-native:cognitiveservices:listAccountKeys" not in result["calls"]
    variables = resources["worker"]["inputs"]["template"]["containers"][0]["env"]
    assert "ISA9000_AZURE_OPENAI_API_KEY" not in {item["name"] for item in variables}


def test_foundry_in_the_application_group_keeps_the_worker_grant():
    resources = run_stack(
        "dev",
        {
            "isa9000:createResourceGroup": False,
            "isa9000:resourceGroup": "Existing-Client-Group",
            "isa9000:foundry": {
                "endpoint": "https://client.cognitiveservices.azure.com/",
                "resourceId": "/subscriptions/00000000-0000-0000-0000-000000000000/"
                "resourceGroups/existing-client-group/providers/"
                "Microsoft.CognitiveServices/accounts/client",
            },
            "isa9000:models": [
                {"role": "text", "name": "gpt-6.1-sol"},
                {"role": "image", "name": "gpt-image-2.5-sunburst"},
                {"role": "transcribe", "name": "gpt-transcribe"},
            ],
        },
    )["resources"]
    assert resources["worker-foundry"]["inputs"]["principalId"] == "worker-identity-principal"


def test_immutable_image_and_every_employee_can_sign_in():
    digest = "sha256:" + "a" * 64
    result = run_stack(
        "prod",
        {
            "isa9000:imageTag": digest,
            "isa9000:allowedIps": ["192.0.2.0/24"],
            "isa9000:groupClaims": "",
        },
    )
    resources = result["resources"]
    assert resources["auth-service-principal"]["inputs"]["appRoleAssignmentRequired"] is False
    assert resources["auth-app"]["inputs"]["groupMembershipClaims"] == ["SecurityGroup"]
    assert resources["web"]["inputs"]["configuration"]["ingress"]["ipSecurityRestrictions"] == [
        {"name": "allowed-0", "action": "Allow", "ipAddressRange": "192.0.2.0/24"}
    ]
    for name in ("web", "worker", "watchdog"):
        assert resources[name]["inputs"]["template"]["containers"][0]["image"].endswith(
            "@" + digest
        )


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"isa9000:ingress": "internal"}, "network and certificate contract"),
        ({"isa9000:ingress": "custom-domain"}, "staged DNS and domain-binding contract"),
    ],
)
def test_unresolved_or_protected_settings_stop(overrides, message):
    result = subprocess.run(
        [sys.executable, str(RUNNER), "dev", json.dumps(overrides)],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0
    assert message in result.stderr
