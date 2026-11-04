# Contributing

## Setup and checks

Use Python 3.13 and uv. The source checks use mocks and require no Azure credentials
or paid model calls:

```bash
uv sync --project services --frozen && uv sync --project infra --frozen
```

```bash
uv run --project services ruff check . && uv run --project services pytest -q services/tests && uv run --project infra pytest -q infra/tests && uvx --from reuse==6.2.0 reuse lint
```

For a browser trial, follow [Install and get started](docs/getting-started.md).
It requires Docker Compose and your own Entra configuration. Real generation and
provisioning spend cloud credits; agree on those runs with the deployment owner.

## Pull requests

- Keep each change focused and explain its effect, validation and migration needs.
- Add regression coverage for behavior changes and update the relevant documentation.
- Preserve server-side authorization, private media access and worker isolation.
- Keep dependency lockfiles and vendored files/notices in sync. Update `REUSE.toml`
  and retain the original license when adding third-party files.
- Use synthetic data in issues, tests and screenshots. Never submit credentials,
  deployment identifiers, company documents, private state or local workstation paths.
- Report vulnerabilities privately using [SECURITY.md](SECURITY.md).

The project's interaction rules are in [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
For ordinary bugs or installation questions, use the repository's GitHub issues
with the version, sanitized error and reproduction steps. Do not upload a full
environment file, support dump or cloud resource export.

## Licensing of contributions

Contributions are accepted under Apache-2.0, as described in section 5 of
[LICENSE](LICENSE), without additional terms or a separate contributor agreement.
Only submit work you have the right to contribute, including any required employer
or client permission. Identify third-party material and retain its notices.

The software is provided as is under the license. The project does not promise
commercial support or an SLA. Review generated content before publication.
