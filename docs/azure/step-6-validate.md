---
title: "Step 6: Validate with people and documents"
parent: Install on Azure
nav_order: 8
---

# Step 6: Validate with people and documents

Confirm the dev deployment works for real users before promoting.

Open the dev `webUrl` and complete [Create and listen](../using.md):

1. Confirm Microsoft sign-in and administrator access using real work accounts.
2. After a pilot member signs in, name the group under **Admin → Libraries / Entra groups** and set it to **Enabled**.
3. Assign a creator and publish one small approved podcast.
4. Confirm a viewer in that group can play it, read transcripts and seek audio.
5. Confirm someone outside the group cannot open its pages or request media.
6. Check the logo, name, review workflow, run history and failure messages.
7. Find the run's reference ID in Application Insights. Verify alerts and retention with operations.
8. Verify the worker's callbacks, managed identities and queue scaling in Azure.
9. Repeat in test with the **same image digest**.
10. Get IT and business owner sign-off before production. Record the deployed digest, backup/recovery plan and credential renewal owners.
11. After production acceptance, send employees the company URL and introduce the first library.

> **Note:** Azure Monitor emails a verification code to the `alertEmail` address. Verify it, or alert emails are not sent.

> **Warning:** Rolling back an image does not roll back database migrations.

Creators and listeners use the browser; IT maintains the service.
