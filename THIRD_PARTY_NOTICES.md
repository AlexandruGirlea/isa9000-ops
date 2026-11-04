# Third-party notices

Original isa9000-ops code and documentation are licensed under Apache-2.0.
Third-party components retain their own licenses; the top-level license does not
relicense them. `REUSE.toml` identifies licensing for files distributed in this source.

## Bundled browser components

The following files are in `services/web/app_portal/static/vendor/vditor/`.
The existing [vendor notice](services/web/app_portal/static/vendor/vditor/NOTICE.txt)
records the Vditor 4.0.1 source archive, checksum and included components.

| Component | Copyright | License and retained text |
|---|---|---|
| Vditor 4.0.1 editor, locale, icons and styles | B3log, b3log.org | [MIT](services/web/app_portal/static/vendor/vditor/LICENSE) |
| Lute parser bundled in Vditor | 2018-present B3log | [MulanPSL-2.0](services/web/app_portal/static/vendor/vditor/licenses/Lute.txt) |
| diff-match-patch bundled in Vditor | 2018 The diff-match-patch Authors | [Apache-2.0](services/web/app_portal/static/vendor/vditor/licenses/diff-match-patch.txt) |
| GopherJS runtime | 2013 Richard Musiol | [BSD-2-Clause](services/web/app_portal/static/vendor/vditor/licenses/GopherJS.txt) |
| Go runtime portions | 2009 The Go Authors | [BSD-3-Clause](services/web/app_portal/static/vendor/vditor/licenses/Go.txt) |
| highlight.js default stylesheet | 2006 Ivan Sagalaev | [BSD-3-Clause](services/web/app_portal/static/vendor/vditor/dist/js/highlight.js/LICENSE) |

Optional diagram, math and highlighting engines are not included in this vendored
subset. Preserve the vendor notices and license texts when redistributing it.

## Installed dependencies and container images

The application installs `isa9000==0.3.0` from PyPI. Its Apache-2.0 license,
NOTICE and third-party notices are included in the installed distribution. Its
browser assets include Bootstrap, Bootstrap Icons and OpenDyslexic under their
own licenses; retain the engine's notices when distributing those assets.

Python dependencies in `services/uv.lock` and `infra/uv.lock`, the Python base
image and its operating-system packages retain their own licenses. For example,
the engine's `lameenc` dependency declares `LGPL-3.0-or-later`; the application
license does not remove dependency redistribution obligations. Review the exact
dependency versions and their upstream terms for your distribution.

The release workflow generates a CycloneDX SBOM from the production image and
uploads it as the `services-sbom` Actions artifact. Retain that inventory with the
image digest in your release records before artifact retention expires. An SBOM
is an inventory, not a license-compliance certification or a replacement for the
actual license texts and any required corresponding source.

The image includes this file, LICENSE, NOTICE and LICENSES under
`/usr/share/licenses/isa9000-ops/`. Dependency notices remain alongside installed
packages and vendored assets.

## Services and local emulators

Azure, model services, container registries and local emulator images have their
own terms. Compose accepts Microsoft's Service Bus/SQL Server emulator EULAs;
review those terms before using the local stack. PostgreSQL and Azurite images
also retain their respective licenses. None of these services is relicensed by
isa9000-ops.

The documentation uses the separately licensed Just the Docs theme, Jekyll plugins
and Mermaid. Product names and logos do not imply endorsement or grant trademark
rights beyond their applicable terms.
