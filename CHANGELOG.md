# Changelog

## 0.1.7

- isa9000 0.3.4: a PDF read that hits a server or connection error on Azure is retried, up to
  4 times, instead of failing the podcast.

## 0.1.6

- isa9000 0.3.3: a long PDF read no longer loses its connection on Azure, and it streams its
  answer, so it no longer fails at the 10-minute timeout.
- Source limits match isa9000: at most 10 sources per podcast, links and files together, links of
  at most 2,000 characters, and PDFs of at most 18 MB and 100 pages. The create page shows them.
- A failed run shows the reason when its creator can fix it: a source that cannot be read, too
  many sources or a PDF that is too large. Other failures keep the generic message.

## 0.1.5

- Add private Azure service connectivity and separate database migration access.
- Run migrations before application rollout and improve worker credential handling.
- Update generation limits, source checks and deployment documentation.

## 0.1.4

- Queued podcasts run in parallel, up to the worker limit: a worker keeps its task's queue
  message locked until the task ends, so a running task no longer holds back the next one.
- A run that takes longer than 110 minutes is stopped and reported as failed before the job's
  2-hour limit ends the worker.
- Worker executions now exit as soon as their task ends. They used to hang until the job's
  2-hour timeout, and a hung execution held back the next queued podcast.
- Azure SDK logs below WARNING are no longer exported. The exporter's own upload messages had
  been exported again in an endless loop, sending several log records a second.
- isa9000 0.3.2: source checks judge only the link given, so a site that answers unknown pages
  with its home page is no longer rejected; following links also reads the same site's other
  subdomains; a followed page with too little text is skipped instead of failing the run.
- With "Review transcripts before recording" unticked, a podcast is recorded even when isa9000
  asks for review; when a run does stop for review, its page shows isa9000's reason.
- Retrying a failed podcast lets you change "Review transcripts before recording".
- A podcast's Manage page shows the prompt it was created from.
- The follow-links hint says 25 linked pages in total, from the same site and its subdomains.
- A release from a private repository owned by a user, where GitHub cannot store attestations,
  skips attesting its image instead of reporting an error. Elsewhere the release still attests
  the image, and continues without an attestation where GitHub refuses one.
- The workflows use GitHub Actions releases that run on Node.js 24, which replaces the
  deprecated Node.js 20.
- `bootstrap.sh` stops before calling GitHub or Azure when the repository, storage account or
  registry name is invalid, also under Bash 3.2, the macOS default.
- The docs show how to grant yourself access to an environment's Pulumi state and how to add the
  callback to an existing sign-in registration through Microsoft Graph. They also note that dev
  and test start the web app cold after idle unless `isa9000:webMinReplicas` is 1, and that
  Azure Monitor sends no alert emails until the alert address is verified.

## 0.1.3

- A company copy can build its own image. Pass a registry name to `bootstrap.sh` as a sixth
  argument to create a shared Azure Container Registry, then set the repository variable
  `AZURE_CONTAINER_REGISTRY` and `isa9000:registry` in every stack. Each release then builds,
  tests and scans the image, pushes it to that registry and deploys the same digest to dev, test
  and prod. The web app, worker and watchdog pull it with their managed identities (AcrPull);
  the registry has no admin user or password. Existing installations that adopt it run only the
  registry commands from `bootstrap.sh`. While Azure applies the new AcrPull grants, an
  environment's first deployment from the registry can fail to pull the image; rerun the failed
  job. Without a registry, the published image is used as before.
- The release attests a company-built image where GitHub allows it. Private repositories need
  GitHub Enterprise Cloud for attestations; without it, the release continues unattested.
- A release can stop after dev or test: set the repository variable `LAST_ENVIRONMENT` to `dev`
  or `test`, and the later environments show as skipped instead of failing. Unset, a release
  deploys dev, test and prod as before.
- `bootstrap.sh` can set up fewer environments: `ENVIRONMENTS=dev`, for example, creates only
  dev's resource group, state container and identity. The default is `dev test prod preview`;
  dev is required, and prod needs test. `REGISTRY_SKU` chooses the registry tier: `Basic`,
  `Standard` (the default) or `Premium`.

## 0.1.2

- `pulumi destroy` no longer stops at the PostgreSQL TLS settings. Azure rejects the provider's
  reset of a server parameter, so the stack now leaves both parameters on the server, which
  removes them when it is deleted.
- Log alerts are created after the Container Apps environment, so a first deployment no longer
  fails when Azure briefly reports the new log workspace as missing.

## 0.1.1

- **Upgrade note:** groups no longer become libraries by themselves. On upgrade, groups that
  own or receive a podcast become enabled libraries and all other groups are
  hidden. Nothing is deleted. Enable further groups under **Admin → Libraries / Entra groups**.
- **Upgrade note:** everyone in the company's Entra directory can now sign in, including invited
  guests. People with no library access see no libraries. `authApp: create` no longer requires
  assignment to the application, and `groupClaims` defaults to `SecurityGroup` so each person's
  groups appear in the admin. Set `groupClaims: ApplicationGroup` to send only assigned groups.
  With your own registration, set **Assignment required** to **No** yourself.
- Sign-ins list each group under **Admin → Libraries / Entra groups**, hidden and named after its Entra ID.
  Administrators open a group to rename and enable it.
- Administrators control whether a group is a library: hidden or enabled. Hiding one revokes
  portal access and keeps all data. Viewers see a library once it has a published podcast;
  creators and administrators see it straight away. **New library** is now **Add podcast**.
- People whose token has no usable group list (Entra group overage, missing or malformed
  claims) can sign in instead of getting a 403 error. Stale memberships never grant access.
- New optional `isa9000:graphGroups`: when a token has too many groups to list them, the
  application asks Microsoft Graph with that person's own sign-in, so their libraries and
  administrator access work automatically. Off by default. Grant admin consent first, because
  its permissions are requested at every sign-in; see group overage in the operator guide.
- Confirmations and notices use the application's own dialogs instead of browser pop-ups, and
  the admin no longer opens pop-up windows for related records.
- The admin identifies people by name and email.
- Signing out, or a session timing out, ends only this application's session and returns to the
  landing page. People stay signed in to Microsoft and everything else they have open.
- **Contact** lets creators send requests to administrators, who track them in the admin.
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
- The image now installs Debian's latest security updates when it is built, including the zlib
  fix for CVE-2026-85091.
- Removed the legacy dev sign-in mode (`ISA9000_AUTH=dev`) with `make seed` and `make local-user`.
  `make fake-user` now needs a library: `make fake-user ARGS="--group LIBRARY-ID"`.

## 0.1.0

- First release: the hosted, multi-user isa9000 on Azure.
- Infrastructure examples and mocked test stacks consistently use isa9000's
  recommended Azure models; test provisioning values are explicitly synthetic.
- Apache-2.0 license, third-party notices, REUSE licensing metadata, contribution
  guidelines and a private security reporting policy. Images include license texts.
- CI licensing checks and a CycloneDX image SBOM in release artifacts. Azure PR
  previews are opt-in through the repository variable `ENABLE_AZURE_PREVIEW=true`.
