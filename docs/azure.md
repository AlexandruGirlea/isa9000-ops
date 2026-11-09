---
title: Install on Azure
nav_order: 3
---

# Give employees a shared company URL

This is the IT team's installation sequence. Complete the
[local trial](getting-started.md) first. Employees will use the resulting web
address; they will not use GitHub or install the software themselves.

## Before provisioning

**The current supported starting point is an Azure pilot on a Container Apps
HTTPS address.** Azure application deployment is not yet validated end to end.

Company-domain production onboarding must be completed before treating this as a
production recipe. Custom domains, supplied certificates, internal ingress and
private Postgres networking are not yet supported by the provisioning code.

The deployment uses the published `ghcr.io/alexandrugirlea/isa9000-ops` image by
default. A company copy can instead build, test and scan its own image in its
release workflow and deploy it from its own Azure Container Registry; see
[step 3](#3-bootstrap-azure-deployment-access).

Deployments use Azure HTTPS ingress and certificates. The production Container
Apps VNet supports zone redundancy; it does not provide a WAF or private endpoints
for the database, queue, storage or vault.
If a company domain or private networking is required from day one, complete that
implementation before provisioning. [Discuss a pilot with us](https://girlea.ro).

For enterprise production, IT supplies the network ranges, private DNS and approved
admin hostname. The worker must have no route to Postgres or unrelated company
services; permit only the required model, storage, queue and callback destinations,
plus approved public source access. URL validation is an additional control, not
a sandbox for a compromised document parser. Process/identity isolation remains
required before trusting untrusted parsers with sensitive deployment credentials.

Production onboarding also needs runtime/migration database roles, a privileged
administrator group, upload malware scanning with a
scan-result gate, and tested capacity/recovery settings. These are explicit
prerequisites, not controls already provisioned by the current pilot template.
The image release gate blocks HIGH and CRITICAL findings; resolve or review
remaining vendor findings before an approved rollout.

Use [Client networking](networking.md) for the resource IDs IT supplies,
commented Pulumi examples and the integration/verification sequence.

## Production resource defaults

Setting `isa9000:environment: prod` selects this cost-conscious baseline:

| Service | Production default |
| --- | --- |
| Django | 2 vCPU / 4 GiB per replica; minimum 1, maximum 5; HTTP autoscaling |
| Workers | 2 vCPU / 4 GiB per execution; 0–5 concurrent executions, one replica per execution |
| Container Apps environment | Consumption workload profile, zone redundancy, dedicated VNet/subnet |
| PostgreSQL 16 | General Purpose `Standard_D2ds_v5` (2 vCPU / 8 GiB), no standby |
| Database disk | 128 GiB, automatic growth enabled |
| Database backups | 35-day retention, point-in-time restore, geo-redundant backup |
| Podcast storage | Standard GZRS, blob versioning, 30-day blob/container deletion recovery |
| Queue | Service Bus Standard, public endpoint, TLS and managed identity; local keys disabled |

These are capacity and data-recovery defaults, not a guarantee of uninterrupted
service. Minimum one Django replica saves idle cost but cannot keep a second live
copy in another zone. Azure spreads replicas when multiple copies run. PostgreSQL
has no HA standby or automatic standby failover; recovery can require restoring
backups and switching the application to a new server. Regional copies are
asynchronous, so recent writes can be lost during a regional disaster. Service Bus
Standard has no Private Link or configured cross-region queue replication. Test
restore procedures, load and AI quotas before relying on the installation.

Production creates an external Container Apps environment in its own VNet. Default
ranges are `10.42.0.0/16` for the VNet and `10.42.0.0/23` for the subnet; override
`containerAppsVnetCidr` and `containerAppsSubnetCidr` before creation if they overlap
your network. The IPv4 subnet must be within the VNet and at least /27 in size. It
is delegated to `Microsoft.App/environments`. Register `Microsoft.Network` along
with the other resource providers. Choose a region supporting Container Apps zones,
GZRS, the database SKU and geo backups. See Microsoft's
[zone redundancy requirements](https://learn.microsoft.com/en-us/azure/container-apps/how-to-zone-redundancy).

For existing installations, zone redundancy and the infrastructure subnet need a
new Container Apps environment and a planned cutover, including the changed host
and Entra callbacks. Geo database backups must be configured at server creation;
storage redundancy conversions depend on the existing account and region. Database
disks cannot shrink. Review replacement/conversion steps and preserve data before
applying these defaults to an existing stack; do not bypass resource protection.

Dev/test retain small compute, Burstable PostgreSQL, LRS storage and a Basic queue.
Existing stack overrides such as `webMinReplicas`, `webMaxReplicas`, `postgresSku` and
`postgresTier` take precedence over defaults; review them when adopting this profile.

## Key Vault is included

Every environment creates its own Key Vault in the application resource group.
Database, Django, callback, Entra and payload-encryption credentials use managed
identity references. The worker can read only the callback secret and payload
key. The vault uses RBAC, 90-day recovery, purge protection and audit logging;
production also has Pulumi deletion protection. No extra client vault setup is
required. Its endpoint remains publicly addressable but authenticated for the
pilot; private connectivity follows the client networking guide.

Pulumi exports `keyVaultName` and `keyVaultId` for network integration. Existing
bootstrap identities need their role-assignment condition updated to include
Key Vault Secrets User (`4633458b-17de-408a-b874-0445c86b69e6`) before deploying
these references. Review that exact change; do not rerun bootstrap blindly or
grant Key Vault administrator access to the workloads.

## 1. Prepare your company repository and accounts

Create a company-controlled GitHub copy of the complete source, enable Actions,
and clone that copy. Keep your deployment configuration there. No engine fork
is needed.

The public source contains templates and synthetic test settings only. Keep real
subscription, tenant, application and group IDs, endpoints, credentials and Pulumi
state in your private deployment workspace. Before redistributing a source folder,
remove its deployment files, local notes, IDE metadata and generated caches, and
review Git history. `.gitignore` does not remove existing files or past commits.

IT needs:

- An Azure subscription and region, with approved spending and model availability.
- Azure CLI, GitHub CLI, Pulumi and uv on the setup computer.
- Permission to create resources and role assignments for the one-time bootstrap.
- An Entra administrator for sign-in registration and group configuration.
- An application administrator group, pilot-team groups and a pilot creator.
- The approved container digest, such as `sha256:…`, from the image publisher, or a
  registry name if your releases build the image.

Decide who owns retention, alerts, backup/recovery and application updates.
Azure hosting, storage and model usage are paid through your organization's
accounts; the local simulation does not estimate those costs.

## 2. Apply your brand and stack settings

Follow [Make it yours](branding.md). Then copy `infra/Pulumi.example.yaml` to
`infra/Pulumi.dev.yaml` and `infra/Pulumi.test.yaml` and fill in your organization's
nonsecret settings. Configure each environment separately. These files are
git-ignored; store each one's contents in its environment's `PULUMI_STACK_CONFIG`
variable (see the table below), and the workflow writes it before deploying.

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

Use the actual endpoint, resource ID and deployment names supplied by your AI
platform team. Speech defaults to Serena DragonHD. The worker receives access
to the existing resource through managed identity. Check that all required AI
operations work under that identity during acceptance.

For test, change the environment and resource group. Use the same approved image
digest. New Foundry provisioning is also supported with `foundry: create`,
but each model entry then additionally requires its model/version/format/SKU/
capacity from the AI platform team.

To deploy the image your releases build, add to every environment the
`isa9000:registry` value that bootstrap prints (step 3), the registry's resource ID.
The release then sets `imageTag` to the digest it built; keep the last deployed
digest in `imageTag` so previews match. Without `registry`, the published image is used.

Files in `infra/tests/stacks/` are mocked test fixtures using the same recommended
model names. Their versions, SKUs and capacities are synthetic; start real
deployments from `infra/Pulumi.example.yaml` and your platform team's settings.

`ingress: public` means a public HTTPS endpoint protected by Entra sign-in.
It does not mean anonymous podcast access or private network isolation.
Postgres currently uses an Azure-services firewall exception.

Deployment inputs belong in GitHub environment secrets and private Pulumi state;
runtime credentials are stored in the application's Key Vault.
Never commit credentials or encrypted secret values in stack YAML.

## 3. Bootstrap Azure deployment access

Sign in to the intended tenant and subscription:

```bash
az login --tenant "YOUR-TENANT-ID" && az account set --subscription "YOUR-SUBSCRIPTION-ID"
```

After the platform team reviews and approves its permissions, run:

```bash
bash bootstrap.sh "YOUR-SUBSCRIPTION-ID" "YOUR-REGION" "YOUR-STATE-RESOURCE-GROUP" "youruniquestorageaccount" "YOUR-ORG/isa9000-ops"
```

This **creates Azure resources and role assignments**: private Pulumi state
storage, three deployment identities and a preview identity. It prints their
IDs and backend settings. The repository argument must name your company copy.

To set up only some environments, list them in `ENVIRONMENTS`, for example
`ENVIRONMENTS=dev` for a trial or `ENVIRONMENTS="dev test"`; the default is
`dev test prod preview`. Dev is required: releases start there, its identity pushes
images, and preview reads its state. Prod also needs test, because releases reach prod
only through test. Bootstrap creates no identity, resource group, state container or
delete lock for the environments left out. Set `ENVIRONMENTS` again on every rerun,
listing all the environments you use; without it, a rerun sets up all four.

It first reads that repository's OIDC subject format through `gh api`, because new
GitHub repositories use subjects containing owner and repository IDs. Sign in with
`gh auth login`, or set `GITHUB_OIDC_SUBJECT_PREFIX` to the part before
`:environment:ENV`. Rerunning the script updates the federated credentials.

Deployment identities receive Contributor only on their environment resource group
and conditional Role Based Access Control Administrator for the required workload
roles and service principals. Each identity can access only its environment's state
container. Preview reads only dev state. Bootstrap creates the application resource
groups first; use their names in stack settings with `createResourceGroup: false`.
The shared bootstrap resources are retained outside the application stacks.

To build the image in your own releases, add a sixth argument: a new, globally unique
registry name of 5–50 lowercase letters and digits. Bootstrap then also creates a
Standard Azure Container Registry with its admin user disabled in the state resource
group, shared by all environments; set `REGISTRY_SKU` to `Basic` for a trial or to
`Premium` for private endpoints. Pass the registry and `REGISTRY_SKU` again on every
rerun; without `REGISTRY_SKU`, a rerun changes the registry to Standard.
The dev deployment identity may push images to it,
and the dev, test and prod identities may grant only AcrPull on it to service principals.
Because the registry is shared, each of them can also remove any environment's AcrPull,
so a compromised dev or test identity could stop prod from pulling new revisions; protect
all three environments as described in step 4.
Pulumi grants AcrPull to each environment's web, worker and watchdog identities, which
pull the image without registry passwords. Bootstrap prints `AZURE_CONTAINER_REGISTRY`
and the `isa9000:registry` value. For an existing installation, review the registry
commands in `bootstrap.sh` and run only those. Azure can take a few minutes to apply new
AcrPull grants, so an environment's first deployment from the registry can fail with an
image pull authorization error; rerun the failed job.

With `authApp: create`, an Entra administrator must grant each deployment
identity Microsoft Graph `Application.ReadWrite.OwnedBy`. Pulumi then creates
a tenant-only sign-in registration with the Azure callback automatically.
Everyone in the directory, including invited guests, can sign in; libraries still
depend on their groups. To handle people in too many groups automatically, see
[group overage](operator-guide.md#group-overage).
An existing IT-managed registration is an alternative; see
[registration details](operator-guide.md#11-bootstrap-and-secrets).

IT must preregister the Azure resource providers used by the deployment. If
Foundry lives outside the application resource group, its owner must grant the
worker **Cognitive Services User** there, using the stack output `workerPrincipalId`;
do not expand the deploy identity to the subscription. The stack creates this grant
itself only for an account inside the application resource group.

For an existing installation, plan state migration before using the new backend
containers or changing resource-group ownership. Changing `createResourceGroup`
on an already tracked group can plan deletion. Geo-redundant database backup is
a creation-time setting and can require replacement. Review the Pulumi plan and
recovery procedure before applying; resource protection must remain enabled.
Bootstrap does not remove old subscription-wide assignments. Their owners must
review and revoke them separately after replacement identities are validated.

## 4. Configure GitHub and initialize Pulumi state

Create GitHub environments named **dev**, **test**, **prod** and **preview**. Require a
production reviewer. Restrict dev/test/prod deployments to protected `v*` tags;
this also prevents PR-controlled workflows from requesting those environments.
Require a reviewer for preview. For the first rollout, also require a test reviewer
so the team can validate dev before promotion. Restrict release-tag creation to maintainers.

Create only the environments you bootstrapped. If you deploy only dev, or dev and test,
set the repository variable `LAST_ENVIRONMENT` to `dev` or `test` (step 5). Only prod
has Pulumi deletion protection and a delete lock, so use an installation without prod
for trials, not for data you must keep.

After configuring the protected preview environment, opt in with the
repository-level variable `ENABLE_AZURE_PREVIEW=true`. Without it, public CI runs
its local checks and skips Azure previews.

Set these values in **each** environment:

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

To build your own image, also set the **repository** variable `AZURE_CONTAINER_REGISTRY`
to the registry name from bootstrap.

Use a different state-encryption passphrase for each environment. When initializing
a stack locally, use that environment's matching passphrase and backend.
The setup operator needs Blob Data Contributor access to the state container.
Set `AZURE_STORAGE_ACCOUNT` and `PULUMI_CONFIG_PASSPHRASE` in the terminal from
the approved settings/secret store, then run from the repository root:

```bash
cd infra && pulumi login azblob://pulumi-dev && pulumi stack init dev
```

Repeat separately for test and prod with their matching backend and passphrase.
Set up the protected preview environment as described in the
[operator guide](operator-guide.md#11-bootstrap-and-secrets).
For existing stacks, use `pulumi stack select ENVIRONMENT` instead of initializing
them again. Return to the repository root for Make commands.

For a local preview, set the dev administrator-group secret interactively:

```bash
cd infra && pulumi config set --stack dev --secret isa9000:adminGroupId
```

Pulumi writes this encrypted value into the local stack file. Keep it out of
commits; the release workflow injects it from GitHub secrets. The backend state
is private and must be backed up with its passphrase.

## 5. Preview, then deploy the pilot

From the repository root:

```bash
make preview ENV=dev
```

Review resource names, model access, identities, network exposure and cost before
approving deployment. If preview needs unavailable provider permissions, resolve
that with the platform team; do not silently expand the preview identity.

Commit the reviewed **nonsecret** configuration and branding in the company
repository. Select a new version tag according to your release policy.
The following example starts the deployment workflow; run it only after approval:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

Use an unused version, not this example blindly. In a company copy, the workflow
uses the configured image digest. With `AZURE_CONTAINER_REGISTRY` set, it first
builds the image from the tagged source, runs the tests, vulnerability scan and SBOM,
pushes it to your registry and uses that digest. It deploys dev, runs a smoke check,
then promotes the same digest through test and approved prod. With the repository
variable `LAST_ENVIRONMENT` set to `dev` or `test`, it stops there and shows the later
environments as skipped. GitHub Actions shows progress and the deployment output
includes `webUrl`.

The release also attests the build provenance of an image it pushes. GitHub offers
artifact attestations in private repositories only on GitHub Enterprise Cloud;
elsewhere that step reports an error and the release continues without one.

Leave the production approval gate closed until company-domain support and the
acceptance checks below are complete. A passing health endpoint alone is not
production acceptance.

## 6. Validate with your people and documents

Open the dev `webUrl` and complete [Create and listen](using.md):

1. Confirm Microsoft sign-in and administrator access using real work accounts.
2. After a pilot member signs in, name the group under **Admin → Libraries / Entra groups** and set it to
   **Enabled**. Assign a creator and publish one small approved podcast.
3. Confirm that a viewer in that group can play it, read transcripts and seek audio.
4. Confirm that someone outside the group cannot open its pages or request media.
5. Check the logo, name, review workflow, run history and failure messages.
6. Find the run's reference ID in Application Insights and verify alerts and
   retention with operations.
7. Verify the worker's callbacks, managed identities and queue scaling in Azure.

Repeat in test with the **same image digest**. IT and the business owner sign off
before production. Record the deployed digest, backup/recovery plan and credential
renewal owners. Rolling back an image does not roll back database migrations.

After production acceptance, send employees the company URL and introduce the
first library. Creators and listeners use the browser; IT maintains the service.

## Updates and day-to-day operation

Review a new upstream release, update the pinned image digest in company stack
settings, and promote it through the same environments. With your own registry,
merge the reviewed release into your copy and tag it instead. Branding changes travel
through the company repository and runtime configuration.

The [operator reference](operator-guide.md) covers access, worker behavior,
telemetry, local commands and known deployment limits. Private podcast content
belongs in the application's storage, not in this documentation site.
