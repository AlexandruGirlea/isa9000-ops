---
title: "Step 1: Prepare repository and accounts"
parent: Install on Azure
nav_order: 3
---

# Step 1: Prepare repository and accounts

Set up your private repository, then gather the accounts and approvals IT needs.

1. Create a company-controlled GitHub copy of the complete source.
2. Enable Actions.
3. Clone the copy. Keep your deployment configuration there. No engine fork is needed.

The public source holds templates and synthetic test settings only.

- Keep real subscription, tenant, application and group IDs, endpoints, credentials and Pulumi state in your private deployment workspace.
- Before redistributing a source folder, remove deployment files, local notes, IDE metadata and generated caches. Review Git history.

> **Warning:** `.gitignore` does not remove existing files or past commits.

IT needs:

- [ ] An Azure subscription and region, with approved spending and model availability.
- [ ] Azure CLI, GitHub CLI, Pulumi and uv on the setup computer.
- [ ] Permission to create resources and role assignments for the one-time bootstrap.
- [ ] An Entra administrator for sign-in registration and group configuration.
- [ ] An application administrator group, pilot-team groups and a pilot creator.
- [ ] The approved container digest, such as `sha256:…`, from the image publisher, or a registry name if your releases build the image.

Decide who owns retention, alerts, backup/recovery and application updates.

Your organization's accounts pay for Azure hosting, storage and model usage. The local simulation does not estimate these costs.
