# Changelog

## Unreleased

- **Upgrade note:** groups no longer become libraries by themselves. On upgrade, groups that
  own or receive a podcast become enabled libraries and all other groups are hidden as
  discovered. Nothing is deleted. Enable further groups under **Admin → Libraries**.
- Sign-ins list each group under **Admin → Libraries**, hidden and named after its Entra ID.
  Administrators open a group to rename and enable it.
- Administrators control the library lifecycle: discovered, enabled and archived.
  Archiving revokes portal access and keeps all data. Empty libraries stay hidden unless
  **Show when empty** is on. **New library** is now **Add podcast**.
- People whose token has no usable group list (Entra group overage, missing or malformed
  claims) can sign in instead of getting a 403 error. Stale memberships never grant access.
- The podcast title is optional when creating a podcast; without one, isa9000 chooses it.
- Short podcast descriptions show their formatting on the course page instead of a plain excerpt.
- **Cancel processing** replaces **Stop**: it asks first, deletes what the run created, and
  History shows **User canceled** and who did it.
- Failed runs show creators a short message and a reference. Raw errors and the activity log
  are visible only to administrators.
- The admin shows people by name and who last edited each podcast. **Activity** cannot be
  changed by administrators, and a PostgreSQL trigger refuses updates and deletes.
- Audited local library access with reason, expiry and revocation, independent of Entra
  sync. The user page shows each person's access sources.
- A local **Application administrator** flag and an `app_admin` operator recovery command.
  Library membership never implies administration.
- Admin lists and pickers stay fast at scale: filters, pagination and searchable selectors.
  Portal library lists use set-based queries, pagination and server-side search.
- Fixes: tokens with about 130 to 200 groups were rejected by the web server's header limit,
  and the admin's date pickers and selection counter were blocked by the content security
  policy.
- Fixes found deploying to Azure: worker callbacks use the app's real Azure hostname, the
  dead-letter alert serializes correctly, PostgreSQL changes run one at a time,
  `bootstrap.sh` trusts the OIDC subject GitHub actually issues, the workflows supply the
  federated token Pulumi's Azure Blob backend needs, including after long deployments, and the stack no longer tries to
  assign a role on a Foundry account outside its resource group.

## 0.1.0

- First release: the hosted, multi-user isa9000 on Azure.
- Infrastructure examples and mocked test stacks consistently use isa9000's
  recommended Azure models; test provisioning values are explicitly synthetic.
- Apache-2.0 license, third-party notices, REUSE licensing metadata, contribution
  guidelines and a private security reporting policy. Images include license texts.
- CI licensing checks and a CycloneDX image SBOM in release artifacts. Azure PR
  previews are opt-in through the repository variable `ENABLE_AZURE_PREVIEW=true`.
