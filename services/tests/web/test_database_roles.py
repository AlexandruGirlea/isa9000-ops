from __future__ import annotations

import os
import secrets
from unittest import skipUnless
from unittest.mock import patch

from django.core.management import call_command
from django.db import ProgrammingError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

ROLE = "isa9000_app_test"


@skipUnless(connection.vendor == "postgresql", "Database roles need PostgreSQL.")
class RuntimeRoleTests(TestCase):
    def test_runtime_role_changes_rows_but_not_the_schema(self):
        password = secrets.token_hex(16)
        url = f"postgresql://{ROLE}:{password}@localhost:5432/isa9000"
        with patch.dict(os.environ, {"ISA9000_RUNTIME_DATABASE_URL": url}):
            with CaptureQueriesContext(connection) as queries:
                call_command("migrate_database")
                call_command("migrate_database")  # A second run updates the role, not fails.
        # The server logs failed and audited statements in full, so it must never get the password.
        self.assertEqual([query["sql"] for query in queries if password in query["sql"]], [])
        with connection.cursor() as cursor:
            # Later migrations create tables as this administrator; the role must reach them too.
            cursor.execute("CREATE TABLE later_table (id serial PRIMARY KEY)")
            cursor.execute(f"SET LOCAL ROLE {ROLE}")
            cursor.execute("INSERT INTO later_table DEFAULT VALUES")
            cursor.execute("DELETE FROM later_table")
            cursor.execute("SELECT count(*) FROM django_session")
            for statement in (
                "CREATE TABLE intruder (id integer)",
                "ALTER TABLE django_session ADD COLUMN intruder integer",
                "DROP TABLE django_session",
                "TRUNCATE django_admin_log",
                "DROP TRIGGER audit_log_append_only ON django_admin_log",
                "CREATE ROLE intruder",
            ):
                with (
                    self.subTest(statement),
                    self.assertRaisesRegex(ProgrammingError, "permission denied|must be owner"),
                    transaction.atomic(),
                ):
                    cursor.execute(statement)
