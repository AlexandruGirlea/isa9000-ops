from __future__ import annotations

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase


class StaticTests(SimpleTestCase):
    def test_package_assets_resolve_and_dynamic_assets_pass_to_views(self):
        for path in ("logo.png", "logo-mark-light.png", "logo-mark-dark.png",
                     "vendor/bootstrap.min.css", "fonts/OpenDyslexic-Regular.woff2"):
            with self.subTest(path=path):
                self.assertTrue(finders.find(f"isa9000/{path}"))
        for path in ("logo.svg", "manifest.webmanifest", "unknown.file"):
            with self.subTest(path=path):
                self.assertIsNone(finders.find(f"isa9000/{path}"))
