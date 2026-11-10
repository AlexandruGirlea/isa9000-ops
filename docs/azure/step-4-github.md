---
title: "Step 4: GitHub and Pulumi state"
parent: Install on Azure
nav_order: 6
---

# Step 4: GitHub and Pulumi state

Create the GitHub environments, set their variables and secrets, then initialize one Pulumi stack per environment.

## Environments

1. Create environments **dev**, **test**, **prod** and **preview**. Create only the ones you bootstrapped.
2. Require a production reviewer.
3. Restrict dev/test/prod deployments to protected `v*` tags. This also blocks PR-controlled workflows from requesting them.
4. Require a reviewer for preview.
5. For the first rollout, also require a test reviewer to validate dev before promotion.
6. Restrict release-tag creation to maintainers.
7. After configuring protected preview, set repository variable `ENABLE_AZURE_PREVIEW=true`. Without it, public CI runs local checks and skips Azure previews.

- If you deploy only dev, or dev and test, set repository variable `LAST_ENVIRONMENT` to `dev` or `test` ([step 5](step-5-deploy.md)).

> **Warning:** Only prod has Pulumi deletion protection and a delete lock. Use an installation without prod for trials, not for data you must keep.

## Variables and secrets

Set these in **each** environment:

| Type | Name | Value |
|---|---|---|
| Variable | `AZURE_CLIENT_ID` | That environment's deployment identity from bootstrap |
| Variable | `AZURE_TENANT_ID` | Company tenant ID |
| Variable | `AZURE_SUBSCRIPTION_ID` | Target subscription |
| Variable | `AZURE_STORAGE_ACCOUNT` | Bootstrap state-storage account |
| Variable | `PULUMI_BACKEND_URL` | `azblob://pulumi-dev`, `azblob://pulumi-test` or `azblob://pulumi-prod` |
| Variable | `PULUMI_STACK_CONFIG` | The full contents of that environment's `infra/Pulumi.<env>.yaml` |
| Secret | `PULUMI_DEV_CONFIG_PASSPHRASE`, `PULUMI_TEST_CONFIG_PASSPHRASE` or `PULUMI_PROD_CONFIG_PASSPHRASE` | Only the matching environment's distinct passphrase |
| Secret | `ADMIN_GROUP_ID` | Entra administrator group object ID |
| Secret, if using an existing registration | `AUTH_CLIENT_SECRET` | Its client secret value |

- To build your own image, also set **repository** variable `AZURE_CONTAINER_REGISTRY` to the registry name from bootstrap.
- Use a different state-encryption passphrase for each environment.

## Initialize Pulumi state

When initializing a stack locally, use that environment's matching passphrase and backend.

1. Grant the setup operator Storage Blob Data Contributor on the state container. For dev, grant it to the signed-in account:

   ```bash
   az role assignment create --assignee-object-id "$(az ad signed-in-user show --query id --output tsv)" --assignee-principal-type User --role "Storage Blob Data Contributor" --scope "/subscriptions/YOUR-SUBSCRIPTION-ID/resourceGroups/YOUR-STATE-RESOURCE-GROUP/providers/Microsoft.Storage/storageAccounts/youruniquestorageaccount/blobServices/default/containers/pulumi-dev"
   ```

2. Set `AZURE_STORAGE_ACCOUNT` and `PULUMI_CONFIG_PASSPHRASE` in the terminal from the approved settings/secret store.
3. Run from the repository root:

   ```bash
   cd infra && pulumi login azblob://pulumi-dev && pulumi stack init dev
   ```

4. Repeat separately for test and prod with their matching backend and passphrase.
5. For existing stacks, run `pulumi stack select ENVIRONMENT` instead of initializing again.
6. Set up the protected preview environment as described in the [operator guide](../operator-guide/bootstrap-and-secrets.md).
7. Return to the repository root for Make commands.

## Local preview

For a local preview, set the dev administrator-group secret interactively:

```bash
cd infra && pulumi config set --stack dev --secret isa9000:adminGroupId
```

- Pulumi writes this encrypted value into the local stack file.
- The release workflow injects it from GitHub secrets.

> **Warning:** Keep the stack file value out of commits. Back up the private backend state together with its passphrase.
