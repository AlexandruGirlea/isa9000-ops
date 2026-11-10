---
title: "Step 2: Brand and stack settings"
parent: Install on Azure
nav_order: 4
---

# Step 2: Brand and stack settings

Fill in nonsecret stack settings for each environment, starting from the example file.

1. Follow [Make it yours](../branding.md).
2. Copy `infra/Pulumi.example.yaml` to `infra/Pulumi.dev.yaml` and `infra/Pulumi.test.yaml`.
3. Fill in your organization's nonsecret settings. Configure each environment separately.
4. Store each file's contents in its environment's `PULUMI_STACK_CONFIG` variable (see the table in [step 4](step-4-github.md)). The workflow writes it before deploying.

The `Pulumi.*.yaml` files are git-ignored.

For a pilot using **existing approved Foundry deployments**, the shape is:

```yaml
config:
  azure-native:subscriptionId: YOUR-SUBSCRIPTION-ID
  azure-native:location: YOUR-REGION
  isa9000:environment: dev
  isa9000:resourceGroup: rg-company-audio-dev
  isa9000:createResourceGroup: false
  isa9000:ingress: public
  isa9000:imageTag: "sha256:YOUR-PUBLISHED-IMAGE-DIGEST"
  isa9000:alertEmail: audio-operations@example.com
  isa9000:authApp: create
  isa9000:groupClaims: SecurityGroup
  isa9000:foundry:
    endpoint: https://YOUR-RESOURCE.cognitiveservices.azure.com/
    resourceId: /subscriptions/YOUR-SUBSCRIPTION-ID/resourceGroups/YOUR-AI-GROUP/providers/Microsoft.CognitiveServices/accounts/YOUR-RESOURCE
  isa9000:models:
    - role: text
      name: gpt-6.1-sol
    - role: image
      name: gpt-image-2.5-sunburst
    - role: transcribe
      name: gpt-transcribe
```

- Use the endpoint, resource ID and deployment names from your AI platform team.
- Speech defaults to Serena DragonHD.
- The worker reaches the existing resource through managed identity. During acceptance, check that all required AI operations work under that identity.

> **Note:** `ingress: public` means a public HTTPS endpoint protected by Entra sign-in. It does not mean anonymous podcast access or private network isolation. Postgres has no public access; the apps reach it through a private endpoint in their VNet.

## Test environment

- Change the environment and resource group. Use the same approved image digest.
- `foundry: create` also provisions new Foundry. Each model entry then additionally requires its model/version/format/SKU/capacity from the AI platform team.

## Build your own image

1. Add the `isa9000:registry` value that bootstrap prints ([step 3](step-3-bootstrap.md)) to every environment. It is the registry's resource ID.
2. The release sets `imageTag` to the digest it built.
3. Keep the last deployed digest in `imageTag` so previews match.

Without `registry`, the published image is used.

## Fixtures and secrets

- `infra/tests/stacks/` holds mocked fixtures with the same recommended model names. Versions, SKUs and capacities are synthetic.
- Start real deployments from `infra/Pulumi.example.yaml` and your platform team's settings.
- Deployment inputs belong in GitHub environment secrets and private Pulumi state.
- Runtime credentials are stored in the application's Key Vault.

> **Warning:** Never commit credentials or encrypted secret values in stack YAML.
