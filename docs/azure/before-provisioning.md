---
title: "Before provisioning"
parent: Install on Azure
nav_order: 1
---

# Before provisioning

Check what the supplied Azure stack provides and where it stops before you provision.

The stack provides:

- Public HTTPS ingress on an Azure Container Apps hostname, protected by Microsoft Entra sign-in
- `dev`, `test` and `prod` resource profiles
- Azure HTTPS ingress and certificates
- Private endpoints in each Container Apps VNet for Postgres, Blob storage, Key Vault and stack-created Foundry accounts, with public access disabled
- Zone redundancy in production
- The published `ghcr.io/alexandrugirlea/isa9000-ops` image by default

A company copy can build, test and scan its own image and deploy it from its own Azure Container Registry. See [step 3](step-3-bootstrap.md).

## Needs extra integration

The provisioning code accepts `ingress: public`. These need additional infrastructure integration:

- Custom domains
- Supplied certificates
- Internal ingress
- Postgres inside a company-supplied network

Not provided: a WAF, and a private endpoint for Service Bus. Reused Foundry resources keep their owner's network configuration.

> **Note:** Include any company domain or extra private networking in your deployment plan before provisioning. Resource IDs, commented Pulumi examples and the verification sequence are in [Client networking](../networking.md).

## Enterprise production checklist

1. IT supplies network ranges, private DNS and the approved admin hostname.
2. Give the worker no route to Postgres or unrelated company services.
3. Permit only required model, storage, queue and callback destinations, plus approved public source access.
4. Configure a privileged administrator group.
5. Configure upload malware scanning with a scan-result gate.
6. Test capacity and recovery settings.
7. Resolve or review remaining vendor findings before an approved rollout. The image release gate blocks HIGH and CRITICAL findings.

> **Warning:** URL validation is an additional control, not a sandbox for a compromised document parser. Process/identity isolation remains required before trusting untrusted parsers with sensitive deployment credentials.

> **Note:** Your organization manages administrator group governance and malware scanning. The stack does not provision them.
