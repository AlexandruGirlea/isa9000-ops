from __future__ import annotations

from django.contrib.staticfiles.finders import BaseFinder
from django.core.files.storage import FileSystemStorage

from core.settings import PACKAGE_DIR


class LogoFinder(BaseFinder):
    names = frozenset({"logo.png", "logo-mark-light.png", "logo-mark-dark.png"})

    def find(self, path, find_all=False, **kwargs):
        name = path.removeprefix("isa9000/")
        if path.startswith("isa9000/") and name in self.names:
            result = str(PACKAGE_DIR / name)
            return [result] if find_all else result
        return []

    def list(self, ignore_patterns):
        storage = FileSystemStorage(PACKAGE_DIR)
        storage.prefix = "isa9000"
        for name in sorted(self.names):
            yield name, storage
