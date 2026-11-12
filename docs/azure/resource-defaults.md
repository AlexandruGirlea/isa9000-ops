---
title: "Resource defaults and Key Vault"
parent: Install on Azure
nav_order: 2
---

# Resource defaults and Key Vault

Production defaults and the Key Vault the stack creates in every environment.

## Production resource defaults

Setting `isa9000:environment: prod` selects this cost-conscious baseline.

| Service | Production default |
| --- | --- |
| Django | 2 vCPU / 4 GiB per replica; 1–5 replicas; HTTP autoscaling |
| Workers | 2 vCPU / 4 GiB per replica; 1–5 replicas, one task each; queue autoscaling |
| Container Apps environment | Consumption profile, zone redundancy, dedicated VNet/subnet |
| PostgreSQL 16 | General Purpose `Standard_D2ds_v5` (2 vCPU / 8 GiB), no standby |
| Database disk | 128 GiB, automatic growth |
| Database backups | 35 days, point-in-time restore, geo-redundant |
| Podcast storage | Standard GZRS, blob versioning, 30-day blob/container deletion recovery |
| Queue | Service Bus Standard, public endpoint, TLS, managed identity; local keys disabled |

### What the defaults do not guarantee

These are capacity and recovery defaults, not uninterrupted service. Test restores, load and AI quotas first.

- One minimum Django replica saves idle cost but cannot keep a second live copy in another zone.
- Azure spreads replicas when multiple copies run.
- PostgreSQL has no HA standby or automatic standby failover; recovery can require restoring backups and switching the application to a new server.
- Regional copies are asynchronous; recent writes can be lost in a regional disaster.
- Service Bus Standard has no Private Link or configured cross-region replication.

### Network ranges

Every environment creates an external Container Apps environment in its own VNet. Production adds zone redundancy.

| Setting | Default | Constraint |
| --- | --- | --- |
| `containerAppsVnetCidr` | `10.42.0.0/16` | Override before creation if it overlaps your network |
| `containerAppsSubnetCidr` | `10.42.0.0/23` | IPv4, inside VNet, at least /27, delegated to `Microsoft.App/environments` |
| `postgresSubnetCidr` | /28 after the first subnet | Undelegated private-endpoint subnet; any range inside VNet |

- Register `Microsoft.Network` with the other resource providers.
- Choose a region supporting Container Apps zones, GZRS, the database SKU and geo backups.
- See Microsoft's [zone redundancy requirements](https://learn.microsoft.com/en-us/azure/container-apps/how-to-zone-redundancy).

### Existing installations

> **Warning:** Review replacement/conversion steps and preserve data before applying these defaults to an existing stack. Do not bypass resource protection.

- Zone redundancy and the infrastructure subnet need a new Container Apps environment and planned cutover, including changed host and Entra callbacks.
- Dev/test environments created without a VNet need the same cutover.
- Plan a maintenance window for the network and database-role transition.
- Geo database backups must be set at server creation.
- Storage redundancy conversions depend on the existing account and region.
- Database disks cannot shrink.

### Dev/test profile

- Small compute, Burstable PostgreSQL, LRS storage, Basic queue.
- The web app and the worker each keep at least one replica running; `isa9000:webMinReplicas` below 1 is rejected.
- Existing stack overrides such as `webMinReplicas`, `webMaxReplicas`, `postgresSku` and `postgresTier` take precedence over defaults. Review them when adopting this profile.

## Key Vault is included

Every environment creates its own Key Vault; no extra client vault setup is required.

- It lives in the application resource group.
- Database, Django, callback, Entra and payload-encryption credentials use managed identity references.
- The worker reads only the callback secret and payload key.
- Web app and watchdog log in to Postgres as restricted role `isa9000_app`.
- Only the `migrate` job reads the administrator login.
- Uses RBAC, 90-day recovery, purge protection and audit logging; production adds Pulumi deletion protection.
- Public network access is disabled; workloads use the stack's private endpoint.
- Company network integration follows the client networking guide.
- Pulumi exports `keyVaultName` and `keyVaultId` for network integration.

> **Warning:** Existing bootstrap identities need their role-assignment condition updated to include Key Vault Secrets User (`4633458b-17de-408a-b874-0445c86b69e6`) before deploying these references. Review that exact change; do not rerun bootstrap blindly or grant Key Vault administrator access to the workloads.
