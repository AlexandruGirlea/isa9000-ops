---
title: "Updates and day-to-day operation"
parent: Install on Azure
nav_order: 9
---

# Updates and day-to-day operation

Apply new releases by promoting a new image through the same environments.

- Upstream release: review it, update the pinned image digest in company stack settings, promote.
- Own registry: merge the reviewed release into your copy and tag it.
- Branding changes travel through the company repository and runtime configuration.
- The [operator reference](../operator-guide.md) covers access, worker behavior, telemetry, local commands and known deployment limits.
- Keep private podcast content in the application's storage, not in this documentation site.

## Upgrading from 0.1.7 or earlier

- Library access comes from Entra groups alone. Before upgrading, add everyone who relies on local library access to the library's Entra group. Their records stay in the database but no longer give access.
- Creators are assigned per library. Existing creators keep the enabled libraries they had access to; check them under **Admin → Users**.
- The worker becomes a Container App that keeps one replica running. The first deployment replaces the worker Job and stops if one of its executions is still running, so deploy while no podcast is being generated. The stack output `workerJobName` becomes `workerAppName`.
- `isa9000:webMinReplicas` and `isa9000:workerMaxExecutions` must be at least 1.
