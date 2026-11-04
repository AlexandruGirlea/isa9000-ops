from __future__ import annotations

import hashlib

import pulumi
from pulumi_azure_native import cognitiveservices, resources

config = pulumi.Config("azure-native")
subscription = config.require("subscriptionId")
location = config.require("location")
suffix = hashlib.sha256(f"{subscription}/local".encode()).hexdigest()[:8]
name = f"isa9000-local-{suffix}"
tags = {"project": "isa9000", "env": "local", "managedBy": "pulumi"}
group = resources.ResourceGroup(
    "resource-group", resource_group_name=pulumi.Config("isa9000").require("resourceGroup"),
    location=location, tags=tags,
)
account = cognitiveservices.Account(
    "foundry", resource_group_name=group.name, account_name=name, location=location,
    kind="AIServices", sku={"name": "S0"}, tags=tags,
    properties={"custom_sub_domain_name": name, "public_network_access": "Enabled"},
)
retained = pulumi.Config("isa9000").get_object("retainedModels") or []
models = pulumi.Config("isa9000").require_object("models")
previous = account
for prefix, definitions in (("model", retained), ("model-default", models)):
    for model in definitions:
        previous = cognitiveservices.Deployment(
            f"{prefix}-{model['role']}", resource_group_name=group.name, account_name=account.name,
            deployment_name=model["name"], tags=tags,
            sku={"name": model["sku"], "capacity": model["capacity"]},
            properties={"model": {
                "name": model["model"], "version": model["version"], "format": model["format"],
            }},
            opts=pulumi.ResourceOptions(depends_on=[previous]),
        )
pulumi.export("resourceGroup", group.name)
pulumi.export("accountName", account.name)
pulumi.export("endpoint", account.properties.apply(lambda value: value.endpoint))
pulumi.export("models", {model["role"]: model["name"] for model in models})
