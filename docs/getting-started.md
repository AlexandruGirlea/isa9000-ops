---
title: Install and get started
nav_order: 2
---

# From a source checkout to your first podcast

**Yes: IT starts with a copy of isa9000-ops.** The repository includes the web app,
worker, local containers and Azure deployment tooling. You do not clone or install
the isa9000 engine separately; the application installs its pinned version.

A local trial runs on one computer. A shared installation runs in your Azure
subscription and gives employees a URL. GitHub Pages hosts **these instructions**;
it does not host the employee application or private podcasts.

## Before you begin

| You need | For |
|---|---|
| A source release or supplied source bundle containing `Makefile`, `compose.yaml`, `services/` and `infra/` | Any installation |
| Git, Docker with Compose v2, Make and Python 3 | The local trial |
| A Microsoft Entra tenant and an administrator who can configure an app registration and groups | Employee sign-in, including the local trial |
| A business owner, a small set of source documents and a pilot audience | The first useful podcast |
| An Azure subscription, approved Foundry deployments, a company GitHub repository with Actions, Azure CLI, Pulumi and uv | Shared Azure installation |
| A published isa9000-ops container image and its exact digest, or an Azure Container Registry for your own build | Azure deployment |

An empty repository or a README alone is not an installable release. Confirm
source and image availability before scheduling an Azure rollout.
[Azure deployment requirements →](azure/before-provisioning.md)

## 1. Get the source

For a local evaluation, clone the source repository and work from its root:

```bash
git clone https://github.com/AlexandruGirlea/isa9000-ops.git && cd isa9000-ops
```

If you received a source bundle instead, unpack it and open that folder in your
terminal. For Azure deployment, your IT team should first create a
company-controlled copy in GitHub, then clone **that copy**. Its repository name
will be used for Azure's deployment trust. Keep company configuration private
according to your policy; a public fork is not a place for internal settings.

Do not publish a release tag yet: tags beginning with `v` start Azure deployment
once the workflow has been configured.

## 2. Connect Microsoft sign-in

Ask the Entra administrator to:

1. Create a **single-tenant Web app registration** for the local trial.
2. Register this exact redirect URI:
   `http://localhost:8000/auth/login/aad/callback`.
3. Add **security group claims** to its ID token.
4. Create an administrator security group and a pilot-team security group.
   Add the pilot administrator to both, and pilot employees to the team group.
5. Supply the **tenant ID**, **application/client ID**, **administrator group
   object ID**, and a **client secret value**, through your approved secret channel.

Use the default port 8000 for this walkthrough. A different port also requires
matching redirect settings. This registration is for local use; Azure uses the
callback described in the [Azure guide](azure.md).

Configure the nonsecret identifiers, replacing the quoted placeholders:

```bash
python3 local/configure.py --tenant "TENANT-ID" --client "CLIENT-ID" --admin-group "ADMIN-GROUP-ID"
```

The command generates any missing local application secrets in ignored `.env`.
Add the supplied client **secret value**, not its ID:

```bash
echo 'ISA9000_ENTRA_CLIENT_SECRET=SECRET-VALUE' >> .env
```

All local settings use the `ISA9000_` prefix. You can export them from `~/.zshrc`
instead and remove `.env` yourself; exported values take precedence. Setup leaves
exported secrets in the shell and does not write copies to `.env`.
Keep only the blank `.env.example` in the repository.

## 3. Add your brand and start the app

[Set the company name, logo and colors](branding.md) in `config/`, then run:

```bash
make local-run
```

Docker builds the application and starts its local database, storage, queue,
web app and worker. Open <http://localhost:8000> and select **Sign in with Microsoft**.

A fresh setup uses simulated generation with silent audio and spends no model
credit. Sign-in is real. An existing setup retains its configured worker mode.

**You have reached this step when:** your administrator can sign in and open
**Admin**. An empty **Libraries** page is normal until you publish something.

## 4. Create your first podcast

1. Ask a pilot group member to sign in once. In **Admin → Libraries / Entra groups**, find the group by
   its Entra ID, give it a friendly name and set it to **Enabled**.
2. Ask the pilot creator to sign in once. In **Admin → Users**, set that person
   to **Creator**. Administrators can already create podcasts.
3. Choose **Libraries → Add podcast**, select the pilot library, and create a podcast.
   Use a short title, describe the audience and add a small document.
4. Select **Review transcripts before recording**, then start creation.
5. Read the saved transcripts, make any edits and choose **Approve & record audio**.
6. Sign in as a pilot viewer from that group. Open the library and podcast.

In simulation mode, the sample content checks the workflow; it does not reflect
your documents, and its audio is intentionally silent.
[Creator and listener walkthrough →](using.md)

## 5. Try real audio with approved AI services

For a local pilot using an existing approved Foundry resource, ask IT to supply
its endpoint, key and available deployment names. In your shell or ignored `.env`, set:

```dotenv
ISA9000_AZURE_OPENAI_ENDPOINT=https://YOUR-RESOURCE.cognitiveservices.azure.com/
ISA9000_AZURE_OPENAI_API_KEY=YOUR-KEY
ISA9000_CLI=python -m worker.cli
```

Keep actual values private. The default deployment names are
`gpt-6.1-sol`, `gpt-image-2.5-sunburst` and `gpt-transcribe`; speech uses
`DragonHDLatestNeural` with `en-US-Serena`.
If IT uses different deployment names, make a private copy of the brand TOML,
add its `[models]` mapping, and set `ISA9000_CONFIG_FILE` in your shell or `.env` to that
file's absolute path. Both web and worker must use the same file.

Restart with `make up`. Creating or recording a podcast now spends your AI
service credits. Have the business owner approve a small real trial, then
repeat step 4 with a short document and listen to the result.

**You have reached this step when:** the podcast is based on your supplied
document, its audio plays, and only the intended group can open it.

## 6. Give employees a shared company URL

The local app is for evaluation on one computer. To run a shared Azure installation,
continue with the [Azure installation guide](azure.md). It covers the company
repository, Azure resources, secrets, deployment and acceptance checks.

The supplied stack uses an Azure Container Apps HTTPS address. For a custom domain
or company network integration, review the infrastructure changes in
[Client networking](networking.md) before provisioning.

## If something gets stuck

| Symptom | What to check |
|---|---|
| Microsoft sign-in returns an error | Exact Web redirect URI, tenant/client IDs, secret value/expiry and group claims |
| **Admin** is missing | Administrator group object ID and your membership in the token; sign out and back in after Entra changes. With too many groups, use the [recovery command](operator-guide/sign-in-and-access.md#administrator-access-and-recovery) |
| No libraries appear | Enable the group under **Admin → Libraries / Entra groups**, then publish its first podcast. Viewers see no card for an empty library |
| A notice asks you to request local library access | The sign-in token has no usable group list, usually because of too many groups. See [group overage](operator-guide/sign-in-and-access.md#group-overage) |
| No **Add podcast** button | Creator role or administrator membership |
| Audio is silent | The default worker is a simulation; complete step 5 for real generation |
| Containers fail to start | Run `make ps` and `make logs s=web`; check free ports and Docker resources |

The Service Bus emulator uses SQL Server. On Apple Silicon it needs x86
emulation; Microsoft does not support SQL Server under CPU emulation.
See [local operating notes](operator-guide/run-locally.md).
`make down` stops the local stack and keeps data; `make clean` deletes its data.
