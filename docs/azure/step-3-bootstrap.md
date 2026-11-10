---
title: "Step 3: Bootstrap Azure access"
parent: Install on Azure
nav_order: 5
---

# Step 3: Bootstrap Azure access

Create the private Pulumi state storage and the deployment identities.

1. Sign in to the intended tenant and subscription.

   ```bash
   az login --tenant "YOUR-TENANT-ID" && az account set --subscription "YOUR-SUBSCRIPTION-ID"
   ```

2. Have the platform team review and approve the script's permissions.
3. Run the script.

   ```bash
   bash bootstrap.sh "YOUR-SUBSCRIPTION-ID" "YOUR-REGION" "YOUR-STATE-RESOURCE-GROUP" "youruniquestorageaccount" "YOUR-ORG/isa9000-ops"
   ```

> **Warning:** The script creates Azure resources and role assignments: private Pulumi state storage, three deployment identities and a preview identity.

It prints their IDs and backend settings. The repository argument must name your company copy.

## Choose environments

Set `ENVIRONMENTS` to set up only some environments. Default: `dev test prod preview`.

- `ENVIRONMENTS=dev` for a trial; `ENVIRONMENTS="dev test"` for two.
- Dev is required: releases start there, its identity pushes images, and preview reads its state.
- Prod also needs test, because releases reach prod only through test.
- Bootstrap creates no identity, resource group, state container or delete lock for omitted environments.
- Set `ENVIRONMENTS` on every rerun, listing all environments you use. Without it, a rerun sets up all four.

## GitHub OIDC subject

- Bootstrap first reads the repository's OIDC subject format through `gh api`. New GitHub repositories use subjects containing owner and repository IDs.
- Sign in with `gh auth login`, or set `GITHUB_OIDC_SUBJECT_PREFIX` to the part before `:environment:ENV`.
- Rerunning the script updates the federated credentials.

## Permissions granted

- Deployment identities get Contributor only on their environment resource group.
- They also get conditional Role Based Access Control Administrator for the required workload roles and service principals.
- Each identity accesses only its environment's state container.
- Preview reads only dev state.
- Bootstrap creates the application resource groups first. Use their names in stack settings with `createResourceGroup: false`.
- Shared bootstrap resources are retained outside the application stacks.

## Optional: container registry

Add a sixth argument to build the image in your own releases: a new, globally unique registry name of 5–50 lowercase letters and digits.

- Bootstrap creates a Standard Azure Container Registry with admin user disabled, in the state resource group, shared by all environments.
- Set `REGISTRY_SKU` to `Basic` for a trial or `Premium` for private endpoints.
- Pass the registry and `REGISTRY_SKU` again on every rerun. Without `REGISTRY_SKU`, a rerun changes the registry to Standard.
- The dev deployment identity may push images.
- The dev, test and prod identities may grant only AcrPull on the registry to service principals.
- Pulumi grants AcrPull to each environment's web, worker and watchdog identities, which pull the image without registry passwords.
- Bootstrap prints `AZURE_CONTAINER_REGISTRY` and the `isa9000:registry` value.
- Existing installation: review the registry commands in `bootstrap.sh` and run only those.
- New AcrPull grants can take a few minutes to apply. A first deployment can fail with an image pull authorization error; rerun the failed job.

> **Warning:** The registry is shared, so each of the three identities can remove any environment's AcrPull. A compromised dev or test identity could stop prod from pulling new revisions. Protect all three environments as described in [step 4](step-4-github.md).

## Microsoft Entra sign-in

With `authApp: create`:

1. An Entra administrator must grant each deployment identity Microsoft Graph `Application.ReadWrite.OwnedBy`.
2. Pulumi creates a tenant-only sign-in registration with the Azure callback automatically.

- Everyone in the directory, including invited guests, can sign in; libraries still depend on their groups.
- For people in too many groups, see [group overage](../operator-guide/sign-in-and-access.md#group-overage).
- Alternative: an existing IT-managed registration; see [registration details](../operator-guide/bootstrap-and-secrets.md).

## Providers and Foundry

- IT must preregister the Azure resource providers the deployment uses.
- If Foundry is outside the application resource group, its owner must grant the worker **Cognitive Services User** there, using stack output `workerPrincipalId`.
- Do not expand the deploy identity to the subscription.
- The stack creates this grant itself only for an account inside the application resource group.

## Existing installations

- Plan state migration before using the new backend containers or changing resource-group ownership.
- Geo-redundant database backup is a creation-time setting and can require replacement.
- Review the Pulumi plan and recovery procedure before applying.
- Bootstrap does not remove old subscription-wide assignments. Their owners must review and revoke them separately after replacement identities are validated.

> **Warning:** Changing `createResourceGroup` on an already tracked group can plan deletion. Resource protection must remain enabled.
