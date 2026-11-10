---
title: Architecture
parent: Operator reference
nav_order: 1
---

# Architecture

Reference for where the code and images live and how podcasts are edited, shared and approved.

## Code and images

```
services/
├─ Dockerfile   builds the shared image
├─ worker/      runs the installed isa9000 library
├─ web/         Django
└─ shared/      storage and telemetry
```

| Context | Shared image |
|---|---|
| Local | `services:local` |
| Releases | `ghcr.io/alexandrugirlea/isa9000-ops:<version>` |
| Company copy that builds its own | `REGISTRY.azurecr.io/isa9000-ops:<version>` |

- **Worker command:** `python -m worker`.
- **Worker** installs `isa9000==0.3.0` and invokes its CLI.
- **Django** owns users, permissions, library navigation, saved runs and podcast HTML.

## Editing a podcast

- **Settings:** content settings belong to each podcast; infrastructure settings belong to IT.
- **Click a podcast** to follow its active run, review its transcripts or listen once it is ready.
- **Title** is required; **description** is optional.
- **Edit podcast** on the published player opens **Manage** for administrators and creators in the owning library, including another creator's podcasts. Other libraries remain inaccessible.
- **Administrators and creators in the owning library can update:** title (up to 80 characters), description and cover.
- **Updating** details or the cover rebuilds the published page without model calls; existing audio stays the same.

## Covers

| Rule | Limit |
|---|---|
| Format | Still JPEG, PNG or WebP |
| Shape | Square |
| Pixels per side | 256–4096 |
| File size | At most 5 MiB |
| Best results | 1024 × 1024 |
| Larger images | Resized |

- **Browser** previews valid files.
- **Server** independently verifies and re-encodes them, stripping metadata and trailing content.
- **Rejected:** SVG, animations and malformed images.
- **Changes** require a current revision and are locked during active runs.
- **Published cover** stays available while its replacement is rendered.
- **Audit:** the change is audited without logging image contents.

## Sharing

Administrators can share a podcast with other libraries:

1. Open **Admin → Podcasts → a podcast → Additional libraries**.
2. Select the other libraries.
3. Click **Save**.

- **Members** of those libraries can listen once the podcast is published.
- **Sharing** does not copy files or run generation.
- **Edit and manage:** only creators in the owning library, plus administrators.
- **Remove access:** clear the library from the selection.

> **Warning:** After removal, new page/media requests are denied immediately, but already downloaded or buffered media cannot be recalled.

## Transcript review and approval

When review is needed:

1. Read the transcripts in the on-page dialog.
2. Check the review notes.
3. Confirm **Approve & record audio**.

- **Approval** is audited.
- **Recording** publishes the completed audio to the library.
- **Progress page** offers the same approval action and directs you to any newer active run.

## Transcript editing

- **Edit transcript** is offered in the transcript dialog when no run is active.
- **Fresh approval** is required to save.
- **Previous files** are preserved.
- **Model calls:** none when saving.
- **Only changed episodes** need new audio.
- **Earlier AI scores** apply to the original draft, not your edits.
- **Stale tabs:** concurrent edits and approvals are rejected.
- **Audit:** by actor, episode, time and correlation ID; transcript text is not logged.

> **Warning:** Editing published audio asks you to confirm removing the podcast from the listener library until it is approved and recorded again.

## Narration choices

- **Creators** can choose narration and wording.
- **No access** to model deployments, credentials or another group's content.
- **Run snapshot:** a run keeps those choices, so reopening progress, retrying and recording after review use the same settings.

## Accounts, groups and database

- **Account system:** one, Entra.
- **Directory membership:** one source, Entra groups, optionally supplemented by audited local grants.
- **Application database:** one, Postgres.

See the [architecture overview](../architecture.md) for the main generation path.
