---
title: Licensing and security
nav_order: 9
---

# Licensing and security for your installation

isa9000-ops uses the same Apache-2.0 license as isa9000. Your organization can use,
modify and distribute the project's code under that license's terms. Keep the
required license, attribution and change notices when redistributing it.
Review the [LICENSE](https://github.com/AlexandruGirlea/isa9000-ops/blob/main/LICENSE),
[NOTICE](https://github.com/AlexandruGirlea/isa9000-ops/blob/main/NOTICE) and
[third-party notices](https://github.com/AlexandruGirlea/isa9000-ops/blob/main/THIRD_PARTY_NOTICES.md)
for the applicable terms. Dependencies retain their own licenses.

The container includes project license texts under
`/usr/share/licenses/isa9000-ops/`. Release builds produce a CycloneDX dependency
inventory in the `services-sbom` Actions artifact. Keep it with the approved image
digest. In particular, review the engine's LGPL-licensed encoder and other
dependency obligations when redistributing images. This is not a certification
that a particular deployment satisfies your organization's policies.

## Report security issues privately

Use **alex@girlea.ro** and follow the
[security policy](https://github.com/AlexandruGirlea/isa9000-ops/blob/main/SECURITY.md).
Do not put vulnerabilities, credentials or company content in public issues.
The policy defines reporting scope, supported versions and response targets.

Your IT team owns deployment security, access, retention, backup/recovery, monitoring
and timely updates. Complete the [Azure prerequisites](azure.md#before-provisioning)
and [network integration](networking.md) before approving production. The open-source
license does not provide an operational SLA or commercial support commitment.

For ordinary installation questions and contributions, see the
[contribution guide](https://github.com/AlexandruGirlea/isa9000-ops/blob/main/CONTRIBUTING.md).
Use public or synthetic examples and remove private deployment details.
