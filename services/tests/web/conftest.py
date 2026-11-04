from __future__ import annotations

import os
from pathlib import Path

import django
import pytest
from django.test.utils import (
    setup_databases,
    setup_test_environment,
    teardown_databases,
    teardown_test_environment,
)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
os.environ.setdefault("ISA9000_DJANGO_SECRET_KEY", "web-tests-only-" + "x" * 60)
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault(
    "ISA9000_CONFIG", str(Path(__file__).resolve().parents[3] / "config" / "isa9000.toml")
)
django.setup()


@pytest.fixture(scope="session", autouse=True)
def django_database():
    setup_test_environment()
    database = setup_databases(verbosity=0, interactive=False)
    yield
    teardown_databases(database, verbosity=0)
    teardown_test_environment()
