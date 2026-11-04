from __future__ import annotations

import asyncio
import json
import runpy
import sys
from pathlib import Path

import pulumi.runtime
import yaml
from pulumi.runtime.stack import run_pulumi_func

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
environment = sys.argv[1]
overrides = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
stack_file = Path(__file__).with_name("stacks") / f"Pulumi.{environment}.yaml"
config = yaml.safe_load(stack_file.read_text())["config"]
config.update(
    {
        "isa9000:adminGroupId": "00000000-0000-0000-0000-000000000001",
        "isa9000:alertEmail": "operator@example.invalid",
    }
)
config.update(overrides)
config = {
    key: value if isinstance(value, str) else json.dumps(value) for key, value in config.items()
}


def unwrap(value):
    if isinstance(value, dict):
        if value.get("4dabf18193072939515e22adb298388d") == "1b47061264138c4ac30d75fd1eb44270":
            return unwrap(value["value"])
        return {key: unwrap(item) for key, item in value.items()}
    if isinstance(value, list):
        return [unwrap(item) for item in value]
    return value


class Mocks(pulumi.runtime.Mocks):
    def __init__(self):
        self.resources = {}
        self.calls = []

    def new_resource(self, args):
        self.resources[args.name] = {"type": args.typ, "inputs": unwrap(args.inputs)}
        outputs = dict(args.inputs)
        if args.typ.startswith("random:"):
            outputs["result"] = "mock-generated-value"
        if args.typ == "random:index/randomBytes:RandomBytes":
            outputs["base64"] = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        if args.typ == "azure-native:managedidentity:UserAssignedIdentity":
            outputs.update(clientId=f"{args.name}-client", principalId=f"{args.name}-principal")
        if args.typ == "azure-native:operationalinsights:Workspace":
            outputs["customerId"] = "mock-workspace"
        if args.typ == "azure-native:applicationinsights:Component":
            outputs["connectionString"] = "InstrumentationKey=mock"
        if args.typ == "azure-native:app:ManagedEnvironment":
            outputs["defaultDomain"] = "mock.eastus2.azurecontainerapps.io"
        if args.typ == "azure-native:dbforpostgresql:Server":
            outputs["fullyQualifiedDomainName"] = "mock.postgres.database.azure.com"
        if args.typ == "azure-native:keyvault:Secret":
            outputs["properties"] = {
                "secretUri": f"https://mock.vault.azure.net/secrets/{args.inputs['secretName']}",
            }
        if args.typ == "azure-native:cognitiveservices:Account":
            outputs["properties"] = {
                **outputs["properties"],
                "endpoint": "https://mock.cognitiveservices.azure.com/",
            }
        if args.typ == "azuread:index/application:Application":
            outputs["clientId"] = "mock-auth-client"
        if args.typ == "azuread:index/applicationPassword:ApplicationPassword":
            outputs["value"] = "mock-auth-secret"
        for field in (
            "resourceGroupName",
            "resourceName",
            "accountName",
            "namespaceName",
            "serverName",
            "workspaceName",
            "environmentName",
            "containerAppName",
            "jobName",
            "vaultName",
            "secretName",
        ):
            if field in args.inputs:
                outputs["name"] = args.inputs[field]
        outputs.setdefault("name", args.name)
        return f"/mock/{args.name}", outputs

    def call(self, args):
        self.calls.append(args.token)
        if args.token == "azure-native:authorization:getClientConfig":
            return {
                "tenantId": "mock-tenant",
                "subscriptionId": config["azure-native:subscriptionId"],
                "clientId": "mock-client",
                "objectId": "mock-object",
            }
        if args.token == "azure-native:resources:getResourceGroup":
            return {
                "name": args.args["resourceGroupName"],
                "id": "/mock/existing-group",
                "location": "eastus2",
            }
        if args.token == "azure-native:operationalinsights:getSharedKeys":
            return {"primarySharedKey": "mock-workspace-key"}
        if args.token == "azure-native:cognitiveservices:listAccountKeys":
            return {"key1": "mock-foundry-key"}
        raise AssertionError(f"Unexpected provider invoke: {args.token}")


mocks = Mocks()
pulumi.runtime.set_mocks(mocks, project="isa9000", stack=environment, preview=False)
pulumi.runtime.set_all_config(config, secret_keys=["isa9000:adminGroupId", "isa9000:authApp"])
asyncio.get_event_loop().run_until_complete(
    run_pulumi_func(lambda: runpy.run_path(str(root / "__main__.py")))
)
print(json.dumps({"resources": mocks.resources, "calls": mocks.calls}))
