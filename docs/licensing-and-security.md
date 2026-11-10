---
title: Licensing and security
nav_order: 9
---

# Licensing and security for your installation

isa9000-ops is licensed under **Apache-2.0**, the same license as isa9000.

- Use, modify, rebrand and redistribute it under that license's terms.
- Keep the required license, attribution and change notices when you redistribute it.
  Review the [LICENSE](https://github.com/AlexandruGirlea/isa9000-ops/blob/main/LICENSE),
  [NOTICE](https://github.com/AlexandruGirlea/isa9000-ops/blob/main/NOTICE) and
  [third-party notices](https://github.com/AlexandruGirlea/isa9000-ops/blob/main/THIRD_PARTY_NOTICES.md).
- Dependencies retain their own licenses.

The container includes project license texts under
`/usr/share/licenses/isa9000-ops/`. Release builds produce a CycloneDX dependency
inventory in the `services-sbom` Actions artifact. Keep it with the approved image
digest. In particular, review the engine's LGPL-licensed encoder and other
dependency obligations when redistributing images. This is not a certification
that a particular deployment satisfies your organization's policies.

## Please keep the "Powered by isa9000" credit

We ask you to keep the footer credit, which links to the
[isa9000 GitHub page](https://github.com/AlexandruGirlea/isa9000). It helps more teams
find the project. It is optional: the license does not require it, and
`powered_by = ""` removes it.

## Report security issues privately

Use **alex@girlea.ro** and follow the
[security policy](https://github.com/AlexandruGirlea/isa9000-ops/blob/main/SECURITY.md).
Do not put vulnerabilities, credentials or company content in public issues.
The policy defines reporting scope, supported versions and response targets.

Your IT team owns deployment security, access, retention, backup/recovery, monitoring
and timely updates. Complete the [Azure prerequisites](azure/before-provisioning.md)
and [network integration](networking.md) before approving production. The open-source
license does not provide an operational SLA or commercial support commitment.

For ordinary installation questions and contributions, see the
[contribution guide](https://github.com/AlexandruGirlea/isa9000-ops/blob/main/CONTRIBUTING.md).
Use public or synthetic examples and remove private deployment details.
