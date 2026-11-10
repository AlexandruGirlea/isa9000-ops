---
title: "Step 5: Preview and deploy"
parent: Install on Azure
nav_order: 7
---

# Step 5: Preview and deploy

Preview the changes, get approval, then tag a release to start the deployment workflow.

1. From the repository root, run:

   ```bash
   make preview ENV=dev
   ```

2. Review resource names, model access, identities, network exposure and cost before approving deployment.
3. If preview needs unavailable provider permissions, resolve that with the platform team. Do not silently expand the preview identity.
4. Commit the reviewed **nonsecret** configuration and branding in the company repository.
5. Select a new version tag per your release policy. Use an unused version, not this example blindly. After approval, run:

   ```bash
   git tag v0.1.0 && git push origin v0.1.0
   ```

6. Watch GitHub Actions. The deployment output includes `webUrl`.

The workflow:

- Uses the configured image digest in a company copy.
- With `AZURE_CONTAINER_REGISTRY` set, builds the image from the tagged source, runs tests, vulnerability scan and SBOM, pushes to your registry and uses that digest.
- Per environment: prepares infrastructure and the `migrate` job, waits for migration success, updates web, worker and watchdog workloads, runs a smoke check.
- Promotes the same digest through dev, test and approved prod.
- Stops at `dev` or `test` when repository variable `LAST_ENVIRONMENT` is set to it; later environments show as skipped.

> **Note:** The release attests build provenance of a pushed image. GitHub offers attestations in private repositories only on Enterprise Cloud. A user-owned private repository skips the step; other private repositories without it report an error and the release continues.

Approve production after completing your domain and networking requirements and the acceptance checks in [step 6](step-6-validate.md). Include authentication, access controls and podcast generation, not only endpoint health.
