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
    result = run_stack(environment)
    resources = result["resources"]
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
    assert storage["allowSharedKeyAccess"] is False
    assert storage["allowBlobPublicAccess"] is False
    assert storage["enableHttpsTrafficOnly"] is True
    assert resources["blob-retention"]["inputs"]["deleteRetentionPolicy"]["days"] == (
        30 if environment == "prod" else 7
    )
    assert resources["tasks"]["inputs"]["maxDeliveryCount"] == 3
    assert resources["tasks"]["inputs"]["lockDuration"] == "PT1M"
    assert resources["servicebus"]["inputs"]["sku"]["tier"] == "Basic"
    assert resources["postgres"]["inputs"]["backup"]["backupRetentionDays"] == (
        35 if environment == "prod" else 7
    )
    assert resources["require_secure_transport"]["inputs"]["value"] == "ON"
    assert resources["ssl_min_protocol_version"]["inputs"]["value"] == "TLSv1.2"

    web = resources["web"]["inputs"]
    worker = resources["worker"]["inputs"]
    watchdog = resources["watchdog"]["inputs"]
    vault = resources["vault"]["inputs"]
    assert vault["resourceGroupName"] == storage["resourceGroupName"]
    assert vault["properties"]["enableRbacAuthorization"] is True
    assert vault["properties"]["enablePurgeProtection"] is True
    assert vault["properties"]["softDeleteRetentionInDays"] == 90
    assert resources["vault-audit"]["inputs"]["workspaceId"] == "/mock/logs"
    assert resources["payload-key"]["inputs"]["length"] == 32
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
            assert secret["keyVaultUrl"] == f"https://mock.vault.azure.net/secrets/{key}"
            grant = resources[f"{name}-secret-{key}"]["inputs"]
            assert grant["scope"] == f"/mock/secret-{key}"
            assert grant["principalId"] == f"{name}-identity-principal"
            assert grant["roleDefinitionId"].endswith("4633458b-17de-408a-b874-0445c86b69e6")
    for service in (web, worker):
        config = next(
            secret["value"]
            for secret in service["configuration"]["secrets"]
            if secret["name"] == "isa9000-config"
        )
        parsed = tomllib.loads(config)
        assert parsed["models"] == {
            "text": "gpt-6.1-sol",
            "image": "gpt-image-2.5-sunburst",
            "transcribe": "gpt-transcribe",
        }
        assert parsed["branding"]["brand"] == "isa9000"
        assert service["template"]["volumes"][0]["secrets"] == [
            {"secretRef": "isa9000-config", "path": "isa9000.toml"},
            {"secretRef": "isa9000-logo", "path": "logo.svg"},
        ]
    web_vars = {item["name"]: item for item in web["template"]["containers"][0]["env"]}
    worker_vars = {item["name"]: item for item in worker["template"]["containers"][0]["env"]}
    assert web_vars["ISA9000_AUTH"]["value"] == "easyauth"
    assert web_vars["DATABASE_URL"]["secretRef"] == "database"
    assert "command" not in web["template"]["containers"][0]
    assert web["template"]["containers"][0]["args"][0] == "gunicorn"
    assert "ISA9000_AZURE_OPENAI_API_KEY" not in worker_vars
    assert "DATABASE_URL" not in worker_vars
    assert worker_vars["ISA9000_PAYLOAD_KEY"]["secretRef"] == "payload-key"
    assert web_vars["ISA9000_PAYLOAD_KEY"]["secretRef"] == "payload-key"
    assert all(
        "CONNECTION_STRING" not in name or name == "APPLICATIONINSIGHTS_CONNECTION_STRING"
        for name in web_vars | worker_vars
    )
    assert web_vars["ISA9000_CALLBACK_BASE_URL"]["value"].startswith(
        f"https://isa9000-{environment}-web.internal."
    )
    assert web["configuration"]["ingress"]["allowInsecure"] is False
    assert {probe["type"] for probe in web["template"]["containers"][0]["probes"]} == {
        "Liveness",
        "Readiness",
    }
    assert worker["configuration"]["replicaTimeout"] == 7200
    assert worker["configuration"]["replicaRetryLimit"] == 0
    scale = worker["configuration"]["eventTriggerConfig"]["scale"]
    assert scale["maxExecutions"] == (10 if environment == "prod" else 3)
    assert scale["pollingInterval"] == 10
    assert scale["rules"][0]["identity"] == "/mock/worker-identity"
    assert scale["rules"][0]["metadata"]["messageCount"] == "1"
    assert watchdog["configuration"]["scheduleTriggerConfig"]["cronExpression"] == "*/5 * * * *"
    assert resources["easyauth"]["inputs"]["globalValidation"]["excludedPaths"] == [
        "/",
        "/static/*",
        "/healthz",
        "/internal/*",
    ]
    assert resources["model-text"]["inputs"]["sku"]["capacity"] == (
        250 if environment == "prod" else 100
    )
    assert resources["logs"]["inputs"]["retentionInDays"] == (365 if environment == "prod" else 30)
    assert resources["insights"]["inputs"]["samplingPercentage"] == 100
    assert resources["insights"]["inputs"]["disableLocalAuth"] is True
    for name in ("web-telemetry", "worker-telemetry"):
        permission = resources[name]["inputs"]
        assert permission["scope"] == "/mock/insights"
        assert permission["roleDefinitionId"].endswith("3913510d-42f4-4e42-8a64-420c390055eb")
    for name in (
        "dead-letter-alert",
        "failed-runs",
        "server-errors",
        "denied-access",
        "worker-executions",
        "budget",
    ):
        assert name in resources


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
    assert resources["worker-foundry"]["inputs"]["principalId"] == "worker-identity-principal"
    assert "azure-native:cognitiveservices:listAccountKeys" not in result["calls"]
    variables = resources["worker"]["inputs"]["template"]["containers"][0]["env"]
    assert "ISA9000_AZURE_OPENAI_API_KEY" not in {item["name"] for item in variables}


def test_immutable_image_and_assignment_required():
    digest = "sha256:" + "a" * 64
    result = run_stack(
        "prod",
        {
            "isa9000:imageTag": digest,
            "isa9000:allowedIps": ["192.0.2.0/24"],
            "isa9000:groupClaims": "ApplicationGroup",
        },
    )
    resources = result["resources"]
    assert resources["auth-service-principal"]["inputs"]["appRoleAssignmentRequired"] is True
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
