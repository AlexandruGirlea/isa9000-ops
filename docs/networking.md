---
title: Client networking
nav_order: 8
---

# Connect to your company's network

Client deployments use Azure end to end. The client owns its Azure network, DNS,
firewall and gateway/WAF. isa9000-ops owns
its application resources, identities and Key Vault in its environment resource
group. The default pilot does not alter corporate networking. The examples below
are commented out and are not imported by the Pulumi program. Inside its own VNet the
stack already uses private endpoints for Postgres, Blob, Key Vault and any Foundry
account it creates, with public access disabled on those resources.

Company network integration requires infrastructure changes.
Keep the current unsupported-mode checks until the connections below are completed
and verified in dev. Do not enable production by only removing those checks.

## What IT provides

| Input | Required purpose |
|---|---|
| Subscription, region and application resource group | Application deployment boundary; grants stay scoped to it. |
| Administrator group ID and membership policy | IT provides a role-assignable group or PIM-governed administration policy and controls membership. The application does not create or govern this group. |
| Separate web and worker Container Apps environments, or dedicated subnets to create them | Isolate untrusted document processing from Django and the database. Supply full resource IDs and address ranges. |
| PostgreSQL delegated subnet and linked private DNS zone | Allow PostgreSQL TCP 5432 from the web and Django watchdog networks only. Deny the worker and other client networks. |
| Private endpoint subnet and linked DNS zones | Private Blob, Key Vault and Foundry access from their authorized workloads. Supply DNS forwarding details if IT uses custom DNS. |
| Application hostname, separate admin hostname and certificate ownership | Gateway, TLS, Entra redirects and cookie separation. A second DNS name alone does not isolate admin sessions. |
| Azure Front Door Premium/WAF or an approved Azure gateway | The only public entry, with private origin access and per-IP rate limits; block public `/internal/` requests. |
| Firewall/route policy and DNS resolver | Permit required Azure dependencies and approved source access; deny unrelated internal services. |
| Runner in the approved network | Deployment validation and private-endpoint smoke checks. |

Use the [Container Apps networking requirements](https://learn.microsoft.com/en-us/azure/container-apps/custom-virtual-networks)
for the chosen environment type. Do not copy example address ranges into a company
VNet. The network owner must approve subnet sizes, delegation and routing.

## Allowed connections

| Caller | Allowed destinations |
|---|---|
| Employee browser | Approved application gateway over HTTPS. |
| Gateway | Web origin only; block internal worker callbacks on public routes. |
| Django web | PostgreSQL, Blob, Service Bus sender, its Key Vault secrets, Entra signing keys, telemetry and, with `graphGroups`, Microsoft Graph. |
| Django watchdog | PostgreSQL, its three Key Vault secrets and telemetry. |
| Migration job | PostgreSQL with the administrator login, started by each deployment; its three Key Vault secrets and the image registry. |
| Worker | Assigned Foundry services, upload/course Blob containers, Service Bus receiver, its two Key Vault secrets, private callback host and telemetry. |
| Source fetch subprocess | Approved public HTTP/HTTPS sources; no database, metadata service or other private destination. |

With the default public ingress, the worker calls back over the web app's Azure hostname,
as Azure documents for apps with external ingress. Those routes stay open to the network,
so each call needs the bearer credential and an encrypted payload. Block `/internal/` at
your gateway once one is in front of the app.

The worker can read only `callback` and `payload-key` in Key Vault. It has no
database secret or Django signing key. Sharing the payload key does not grant
database access. Enforce the database restriction at the network boundary too:
if a database credential is compromised, blocked TCP 5432 must still prevent a
worker connection. Application URL checks do not sandbox a compromised parser.

## Inactive Pulumi examples

These are connection examples for existing client resources. They neither create
a VNet nor enable a supported private deployment by themselves. Keep network
resource ownership in the client's infrastructure stack.

```python
# from pulumi_azure_native import app
#
# client_web_environment = app.ManagedEnvironment.get(
#     "client-web-environment", "CLIENT_WEB_ENVIRONMENT_RESOURCE_ID",
# )
# client_worker_environment = app.ManagedEnvironment.get(
#     "client-worker-environment", "CLIENT_WORKER_ENVIRONMENT_RESOURCE_ID",
# )
# postgres_network = {
#     "delegated_subnet_resource_id": "CLIENT_POSTGRES_SUBNET_RESOURCE_ID",
#     "private_dns_zone_arm_resource_id": "CLIENT_POSTGRES_PRIVATE_DNS_ZONE_ID",
#     "public_network_access": "Disabled",
# }
```

For example, the client's network stack can add an endpoint for the application
vault in its own network. The application stack already creates an endpoint in
its VNet. Use `keyVaultId` from the application stack; do not recreate the vault.

```python
# from pulumi_azure_native import network
#
# endpoint = network.PrivateEndpoint(
#     "isa9000-vault-endpoint",
#     resource_group_name="CLIENT_NETWORK_RESOURCE_GROUP",
#     location="CLIENT_REGION",
#     subnet={"id": "CLIENT_PRIVATE_ENDPOINT_SUBNET_RESOURCE_ID"},
#     private_link_service_connections=[{
#         "name": "vault",
#         "private_link_service_id": "APPLICATION_STACK_KEYVAULT_ID",
#         "group_ids": ["vault"],
#     }],
# )
# network.PrivateDnsZoneGroup(
#     "isa9000-vault-dns",
#     resource_group_name="CLIENT_NETWORK_RESOURCE_GROUP",
#     private_endpoint_name=endpoint.name,
#     private_dns_zone_group_name="default",
#     private_dns_zone_configs=[{
#         "name": "vault",
#         "private_dns_zone_id": "CLIENT_KEYVAULT_PRIVATE_DNS_ZONE_ID",
#     }],
# )
```

The vault's private DNS zone is `privatelink.vaultcore.azure.net`; clients still
use its normal `*.vault.azure.net` URI. Follow Microsoft's
[Key Vault Private Link guidance](https://learn.microsoft.com/en-us/azure/key-vault/general/private-link-service).
Connect the existing Blob and managed Foundry endpoints and DNS to the approved
client networks too. For reused Foundry, coordinate connectivity with its owner.
Service Bus Basic (dev/test) and Standard (prod) remain Entra-authenticated over
TLS; private endpoints require a supported higher tier and a client decision.

## Minimum application integration changes

1. In `infra/core.py`, consume the approved web and worker environments, or create
   each with its approved subnet. Update `infra/apps.py` so web and watchdog use
   the web environment and worker uses the separate worker environment. An internal
   environment and a per-app ingress flag are different controls: allow the worker's
   private callback route without opening a public origin.
2. Postgres already has no public access and is reached through the stack's private
   endpoint. In `infra/data.py`, point that endpoint and its DNS zone group at the
   client's subnet and private DNS zone. Moving to a delegated subnet (virtual network
   integration) can require a database migration or replacement; keep resource
   protection and use the client's recovery plan.
3. Connect the stack's Blob, Key Vault and managed Foundry private endpoints and
   DNS to both workload networks and verify resolution/reachability. Their public
   access is already disabled. For reused Foundry, its owner manages endpoints,
   DNS and public access. Preserve working routes during the cutover.
4. In `infra/edge.py` and `infra/apps.py`, connect the gateway, approved hostname,
   certificate and callback hostname. Update `ISA9000_ALLOWED_HOSTS`, the trusted
   callback base URL on both services, and the Entra redirect URI together.
   Implement separate admin host routing/cookies before exposing that hostname.
5. Point `RUNNER_LABEL` in each GitHub environment to the approved network runner.
   Grant only the read/join permissions needed on supplied network resources;
   the network owner creates or approves endpoints and DNS changes. Do not grant
   subscription Contributor to bypass a denied deployment.
6. Review a Pulumi preview, then validate in dev: worker can use its two secrets
   but cannot read the database secret or connect to TCP 5432; Django can use the
   database; callbacks work only privately; gateway and group authorization hold;
   required model/storage/queue calls work. Repeat in test before production.

No application-level encryption setting can replace these route/firewall checks.
Key Vault and the shared payload key are provisioned by the application stack;
the client does not need to create either manually.
