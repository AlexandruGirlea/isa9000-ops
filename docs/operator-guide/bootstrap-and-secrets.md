---
title: Bootstrap and secrets
parent: Operator reference
nav_order: 8
---

# Bootstrap and secrets

Setup steps for sign-in registration, PR previews and Key Vault secrets.

First follow the [Azure installation sequence](../azure.md): company repository setup, stack configuration, bootstrap, GitHub environment values and promotion.

## Use an existing sign-in registration

1. Set `isa9000:authApp` with the registration's `clientId`.
2. Store the secret in the environment secret `AUTH_CLIENT_SECRET`; the workflow injects `clientSecret`.
3. Register `https://YOUR-APP-HOST/.auth/login/aad/callback` as a web redirect URI.
4. Enable ID-token issuance for Easy Auth.
5. Configure the selected group claims.
6. Set **Assignment required** to **No** in the Enterprise application's properties. Otherwise only assigned people can sign in.
7. For `isa9000:graphGroups`, see [group overage](sign-in-and-access.md#group-overage).

- Use a separate registration per environment.
- The local callback is different.

Add the Azure callback in the portal or through Microsoft Graph. On a registration with several URIs, `az ad app update --web-redirect-uris` can fail with "One or more properties contains invalid values". Patch `web.redirectUris` instead, listing every URI to keep:

```bash
az rest --method PATCH --uri "https://graph.microsoft.com/v1.0/applications(appId='YOUR-CLIENT-ID')" --body '{"web":{"redirectUris":["https://YOUR-APP-HOST/.auth/login/aad/callback","https://YOUR-OTHER-URI"]}}'
```

## Set up PR previews

1. Create a protected GitHub environment named `preview` with a required reviewer.
2. Set the variables in the table below on `preview`.
3. Set the only state secret, `PULUMI_DEV_CONFIG_PASSPHRASE`, to a copy of the dev environment's passphrase.
4. After configuring it, set the repository-level variable `ENABLE_AZURE_PREVIEW=true`. Previews are skipped by default in a new checkout.

| Variable | Value |
|---|---|
| `AZURE_CLIENT_ID` | Preview identity |
| `AZURE_TENANT_ID` | Tenant ID |
| `AZURE_SUBSCRIPTION_ID` | Subscription ID |
| `PULUMI_BACKEND_URL` | `azblob://pulumi-dev` |
| `PULUMI_STACK_CONFIG` | The dev stack file |
| `AZURE_STORAGE_ACCOUNT` | Storage account |

- Preview runs only for same-repository branches.
- Prod secrets are never supplied.
- Verify reader-only preview permissions in dev; some providers read resource keys.

> **Warning:** Review the PR's code before approving access to dev secrets.

## How deployment works

- The deployment workflow accepts calls only from the tag-triggered release workflow.
- Upstream publishes an image and GitHub build provenance.
- A company copy uses its configured image, or builds and pushes its own when the repository variable `AZURE_CONTAINER_REGISTRY` is set.
- GitHub environment protection supplies the approval gates.
- Back up private Pulumi state and its passphrase.

## Key Vault secrets access

Each stack creates a Key Vault in its own resource group. It stores the database connection, Django key, callback secret, admin group, Entra application secret and payload key.

- References use each workload's managed identity.
- Grants are per secret; no workload gets vault-wide Secrets User access.

| Workload | Secrets it may read |
|---|---|
| Django web | `database-runtime`, `django-key`, `callback`, `admin-group`, `auth-client-secret`, `payload-key` |
| Django watchdog | `database-runtime`, `django-key`, `admin-group` |
| Worker | `callback`, `payload-key` |
| Migration job | `database` (administrator), `database-runtime`, `django-key` |

- Azure resolves the references; application code reads environment variables and needs no Key Vault client.
- Public branding/model TOML and logos remain inline configuration assets.
- Secret values still exist in encrypted Pulumi state, and processes authorized to use a secret can see it.
- Key Vault does not protect a secret from a compromised process that already holds it.

## Message encryption

- Service Bus uses TLS; callbacks use HTTPS in Azure.
- Task bodies, callback bodies and successful replies also use AES-256-GCM with the same per-environment 256-bit key, a fresh nonce, and authenticated direction/run/action context.
- Plaintext, modified messages and messages used for another run/action are rejected.
- Run-state checks still handle duplicate deliveries.
- Routing and correlation IDs remain visible; generic non-success responses contain no confidential payload.
- The CLI subprocess does not inherit the key.
- Callbacks keep an 8 MiB plaintext limit, with a bounded larger envelope allowance for encryption overhead.

## Upgrade to encrypted messaging

1. Pause new runs.
2. Drain queued and running work.
3. Update web and worker together.

Old plaintext queue messages are rejected.

## Rotate secrets

- Versionless references support new secret versions, but rotation must be coordinated.
- Order: drain jobs, update the credential producer and all consumers, verify propagation, then resume.
- Database passwords, callback keys and Django keys have different availability/session consequences.
- Manage generated values through Pulumi; ad hoc vault edits can be overwritten by a later deployment.
- Audit events go to the existing Log Analytics workspace; no secret values are logged.
- Propagation behavior: Microsoft's [reference and rotation documentation](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets#key-vault-secret-uri-and-secret-rotation).

## Purge protection and teardown

- Vault purge protection retains deleted vaults/secrets for 90 days.
- Routine deploy identities can create/update native secret resources through ARM.
- The pinned provider uses data-plane operations for deletion/recovery.
- An approved teardown or recovery therefore needs a separately authorized operator.

> **Warning:** Do not broaden worker permissions or disable production protection to make cleanup succeed.

See [client networking](../networking.md) for the independent database/network boundary.

## Local development secrets

- Run `python3 local/configure.py` to persist the random local payload key.
- Then recreate web and worker after work has drained.
- Do not regenerate the key independently on either side.
- Local emulators do not use Azure Key Vault.
- Local settings use `ISA9000_` names.
- Exported shell values take precedence over ignored `.env`; setup does not copy exported secrets back into that file.
- Only the blank `.env.example` belongs in git.
