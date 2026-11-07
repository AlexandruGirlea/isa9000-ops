from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from app_portal.forms import CreateForm
from app_portal.models import Course
from core.security import source_url
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from .helpers import SALES, PortalCase


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

    def test_accepts_public_links_and_refuses_mixed_dns(self):
        public = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        private = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        with patch("core.security.socket.getaddrinfo", return_value=[public]):
            self.assertEqual(source_url("https://example.com/a"), "https://example.com/a")
        with (
            patch("core.security.socket.getaddrinfo", return_value=[public, private]),
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

    def test_custom_brand_logo_and_accent_reach_the_pages(self):
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
                response = self.client.get("/static/isa9000/logo.svg")
                self.assertIn(b"Acme", b"".join(response.streaming_content))

    def test_unknown_sign_in_mode_refuses_to_start(self):
        result = subprocess.run(
            [sys.executable, "-c", "import core.settings"],
            check=False,
            cwd=settings.BASE_DIR,
            capture_output=True,
            text=True,
            env={**os.environ, "ISA9000_AUTH": "dev"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be easyauth or entra", result.stderr)
