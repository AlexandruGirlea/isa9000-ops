from __future__ import annotations

import pulumi

import apps
import core
import data
import edge
import monitoring  # noqa: F401

pulumi.export("webUrl", pulumi.Output.concat("https://", edge.azure_host))
pulumi.export("resourceGroup", data.group_name)
pulumi.export("webAppName", apps.web.name)
pulumi.export("workerJobName", apps.worker.name)
pulumi.export("workerPrincipalId", data.worker_identity.principal_id)
pulumi.export("watchdogJobName", apps.watchdog.name)
pulumi.export("migrateJobName", apps.migrate.name)
pulumi.export("keyVaultName", data.vault.name)
pulumi.export("keyVaultId", data.vault.id)
pulumi.export("imageTag", core.image_tag)
pulumi.export("ingress", core.ingress)
pulumi.export("azureHost", edge.azure_host)
