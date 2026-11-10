---
title: Run locally and branding
parent: Operator reference
nav_order: 7
---

# Run locally and branding

Use this page to run the app locally, test with fake users and apply your brand.

## Run locally

Full setup (cloning, Entra registration, configuration, real audio, first podcast): [local installation walkthrough](../getting-started.md).

Use a separate browser profile and a real Entra employee account to check group isolation.

| Command | What it does |
|---|---|
| `make help` | Lists commands |
| `make scale n=3` | Starts three workers |
| `make logs s=worker` | Follows worker logs |
| `make test` | Runs ruff and pytest |
| `make check` | Checks production settings |
| `make down` | Keeps data |
| `make clean` | Destructive, see warning below |
| `make fake-user ARGS="--group LIBRARY-ID"` | Creates a numbered viewer in that library, prints a session cookie |
| `make fake-user n=3` | Issues a fresh session for fake user 3 |

> **Warning:** `make clean` deletes the local database, blobs and queue volumes.

### Fake users

- Fake sessions time out after two hours idle, eight hours maximum.
- Fake sessions cannot become administrators.
- Easy Auth rejects fake sessions when deployed.
- Treat fake-user session cookie values as credentials.

### Emulator

- Messages do not survive restarts; message TTL is limited to an hour.
- Microsoft [does not support SQL Server under CPU emulation](https://learn.microsoft.com/sql/linux/install-upgrade/setup).
- Apple Silicon needs x86 emulation for the emulator's SQL Server dependency.
- Compose accepts the Service Bus and SQL Server EULAs through `ACCEPT_EULA=Y`.
- Setup details: [Microsoft's emulator setup](https://learn.microsoft.com/azure/service-bus-messaging/test-locally-with-service-bus-emulator).

### Container and migrations

- The image uses UID 10001.
- Source and branding are mounted locally; Python changes reload.
- The watchdog checks every five minutes.
- In Azure, the web app's database role cannot change the schema, so a `migrate` job migrates under a PostgreSQL advisory lock.
- The web app sets `ISA9000_MIGRATE_ON_START=false`; without it, the image migrates when it starts.

### Optional models helper

- `local/models.py` is a provisioning helper; it requires `--resource-group`.
- Keep the stack file private: `local/foundry/Pulumi.local.yaml` with an `isa9000:models` list.
- Your AI platform team supplies the model entry fields: `role`, `name`, `model`, `version`, `format`, `sku`, `capacity`.
- Use `preview` before approving `up`.
- State and credentials are stored outside the source folder.

> **Warning:** Provisioning creates billable Azure resources.

## Make it yours

Brand settings: [Make it yours](../branding.md).

- Azure expects the logo at `config/logo.svg`.
- Brand TOML must not include model mappings or credentials.
- A locally configured `ISA9000_CONFIG_FILE` overrides the file from `config/`.
