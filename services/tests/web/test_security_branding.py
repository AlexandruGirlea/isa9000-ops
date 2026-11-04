from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from app_portal.forms import CreateForm
from app_portal.models import Course, Run
from core.branding import site
from core.dev import SALES
from core.security import source_url
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings

from .helpers import ADMIN, PortalCase, principal


class SecurityBrandingTests(PortalCase):
    def test_refuses_private_and_special_source_addresses(self):
        for address in (
            "127.0.0.1",
            "10.1.2.3",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "0.0.0.0",
            "::1",
            "fc00::1",
            "fe80::1",
            "224.0.0.1",
        ):
            with (
                self.subTest(address=address),
                patch(
                    "core.security.socket.getaddrinfo",
                    return_value=[
                        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)),
                    ],
                ),
                self.assertRaises(ValidationError),
            ):
                source_url("https://example.com/file")

    def test_refuses_local_single_word_and_non_http_links(self):
        for url in (
            "http://localhost/a",
            "http://localhost./",
            "http://host/",
            "file:///etc/passwd",
            "ftp://example.com/a",
            "http://me:secret@example.com/",
            "https://evil\\.com/",
            "https://example.com/ bad",
            "http://127.1",
            "http://2130706433/",
        ):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                source_url(url)

    def test_public_http_https_ipv6_and_mixed_dns(self):
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
        with patch("core.security.socket.getaddrinfo", return_value=public):
            self.assertEqual(source_url("https://example.com/a"), "https://example.com/a")
            self.assertEqual(source_url("http://example.com/a"), "http://example.com/a")
        ipv6 = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", 443))]
        with patch("core.security.socket.getaddrinfo", return_value=ipv6):
            self.assertEqual(
                source_url("https://[2606:4700:4700::1111]/"), "https://[2606:4700:4700::1111]/"
            )
        with (
            patch(
                "core.security.socket.getaddrinfo",
                return_value=[
                    *public,
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
                ],
            ),
            self.assertRaises(ValidationError),
        ):
            source_url("https://mixed.example/a")

    def test_invalid_source_is_refused_before_upload_and_queue(self):
        with (
            patch("app_portal.views.storage.save_upload") as upload,
            patch("app_portal.runs.dispatch") as dispatch,
        ):
            response = self.client.post(
                f"/libraries/{SALES}/new/",
                {
                    "request": "Read sources",
                    "title": "Source reading",
                    "links": "http://localhost/private",
                    "uploads": SimpleUploadedFile("source.md", b"Source"),
                },
                **self.headers,
            )
        self.assertContains(response, "public HTTP or HTTPS")
        self.assertEqual(Course.objects.count(), 1)
        upload.assert_not_called()
        dispatch.assert_not_called()

    @override_settings(MAX_FILES=1, MAX_UPLOAD_MB=1)
    def test_upload_count_type_and_size_limits(self):
        files = [
            [SimpleUploadedFile("a.exe", b"Content")],
            [SimpleUploadedFile("a.md", b"a"), SimpleUploadedFile("b.md", b"b")],
            [SimpleUploadedFile("a.txt", b"a" * (1024 * 1024 + 1))],
        ]
        for uploads in files:
            with self.subTest(files=[file.name for file in uploads]):
                form = CreateForm({"request": "Learn", "title": "Learning"}, {"uploads": uploads})
                self.assertFalse(form.is_valid())
                self.assertIn("uploads", form.errors)
        for extension in ("pdf", "txt", "md", "html"):
            form = CreateForm(
                {"request": "Learn", "title": "Learning"},
                {
                    "uploads": [
                        SimpleUploadedFile(f"source.{extension}", b"Content"),
                    ]
                },
            )
            self.assertTrue(form.is_valid(), form.errors)

    def test_custom_brand_logo_accent_on_signin_library_and_admin(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "logo.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><title>Acme</title></svg>'
            )
            config = folder / "isa9000.toml"
            config.write_text(
                '[branding]\nbrand = "Acme Learning"\nlogo = "logo.svg"\n'
                'accent_color = "#123456"\npowered_by = ""\n'
            )
            with patch.dict(os.environ, {"ISA9000_CONFIG": str(config)}):
                for path in ("/", f"/libraries/{SALES}/"):
                    with self.subTest(path=path):
                        response = self.client.get(path, **self.headers)
                        for text in ("Acme Learning", "/static/isa9000/logo.svg", "#123456"):
                            self.assertContains(response, text)
                        self.assertNotContains(response, "Powered by girlea.ro")
                        self.assertContains(response, "isa9000_theme")
                        self.assertContains(response, "theme-toggle")
                response = self.client.get("/static/isa9000/logo.svg")
                self.assertIn(b"Acme", b"".join(response.streaming_content))
                response = self.client.get("/static/isa9000/manifest.webmanifest")
                self.assertEqual(response.json()["theme_color"], "#123456")
                response = self.client.get(
                    "/admin/", HTTP_X_MS_TOKEN_AAD_ID_TOKEN=principal(groups=[SALES, ADMIN])
                )
                self.assertContains(response, "Acme Learning")
                self.assertContains(response, "/static/admin/css/base.css")
                self.assertNotContains(response, "/static/unfold/")

    def test_no_logo_uses_package_logo_and_marks(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "isa9000.toml"
            config.write_text('[branding]\nbrand = "Plain"\n')
            with patch.dict(os.environ, {"ISA9000_CONFIG": str(config)}):
                branding = site()
                self.assertEqual(branding["logo"], "logo.png")
                self.assertEqual(
                    set(branding["marks"].values()),
                    {
                        "logo-mark-light.png",
                        "logo-mark-dark.png",
                    },
                )

    def test_dev_auth_refuses_production_startup(self):
        result = subprocess.run(
            [sys.executable, "-c", "import core.settings"],
            check=False,
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
            env={**os.environ, "DEBUG": "false", "ISA9000_AUTH": "dev"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires DEBUG=true", result.stderr)

    @override_settings(AUTH_MODE="dev", DEBUG=True)
    def test_seed_runs_stub_uploads_fixture_and_queues_render(self):
        seen = []

        def upload(folder, prefix):
            seen.extend(path.name for path in folder.iterdir())

        with (
            patch("app_portal.management.commands.seed.storage.upload_folder", side_effect=upload),
            patch("app_portal.runs.dispatch") as dispatch,
            patch("app_portal.management.commands.seed.telemetry.setup"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                call_command("seed", stdout=StringIO())
        self.assertIn("podcast.json", seen)
        self.assertIn("01 - Getting started.mp3", seen)
        self.assertIn("01 - Getting started.txt", seen)
        run = Run.objects.exclude(pk=self.run.pk).get()
        self.assertEqual(run.task, "render")
        dispatch.assert_called_once()

    def test_seed_refuses_hosted_mode(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("seed")
