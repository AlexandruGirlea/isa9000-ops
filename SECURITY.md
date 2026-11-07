# Security

## Reporting a vulnerability

Email **alex@girlea.ro**, the same security contact as isa9000. Do not open a
public issue or pull request with vulnerability details.

Include the affected version or commit, container digest if available, impact,
and minimal reproduction steps using synthetic data. Do not send credentials,
tenant or group identifiers, company documents, database dumps or private Pulumi
state. Arrange a private transfer with the maintainer if sensitive evidence is needed.

We aim to acknowledge reports within 3 working days and provide a fix or a plan
within 30 days. Coordinate public disclosure with the maintainer so operators
have time to update. These targets are not a support or availability SLA.

## Supported versions

Security fixes target the latest release. Until the first release, reports against
the default branch are accepted on a best-effort basis. Older releases and custom
forks have no separate maintenance commitment; operators must apply relevant fixes.

## Scope

Reports concerning the web app, worker, infrastructure or delivery workflows are
welcome, including:

- Bypassing Entra sign-in, account deactivation, group membership or creator/admin roles.
- Access to another library's documents, transcripts, audio, covers or private media.
- Forged worker callbacks, unauthorized task execution or cross-run message reuse.
- Source URLs reaching private services, unexpected file access or script injection.
- Credentials, company content or personal data exposed through logs, errors,
  generated pages, images, repository contents or build artifacts.
- Excessive workload/deployment privileges or unauthorized changes through CI.

Test only systems you own or have permission to assess. Public product/demo links
do not grant permission to test those deployments.

## Deployment responsibilities

Each organization runs its own installation and controls identity, networking,
secrets, data retention, backups and updates. Configured Azure/AI services process
the content needed by the application. Public source URLs are fetched by the worker.
Review the [data flow](docs/architecture.md) and your providers' terms.

Local Compose is for a loopback trial and includes public emulator credentials.
Real Entra configuration is still required. Do not reuse emulator credentials or
expose that stack as a production installation.

The [Azure prerequisites](docs/azure.md#before-provisioning) and
[networking guide](docs/networking.md) identify unfinished production integration
and validation. This policy does not assert certification, regulatory compliance
or readiness for an organization's production environment.

If credentials are exposed, revoke or rotate them in the owning system and follow
your incident process. Removing a file or rewriting history does not revoke a key.

## Maintainer controls

Review dependency updates at each release. CI checks licensing, tests and the
container vulnerability scan; releases retain image provenance and an SBOM.
Review proposed dependency changes, bundled notices and scan findings before release.

Repository administrators must configure branch/release protections, required
reviews and the protected deployment environments, and enable available secret
scanning, push protection and dependency alerts. These GitHub settings are not
enabled by adding this file. Azure preview access is explicitly opt-in; review
same-repository PR code before approving access to private deployment settings.
