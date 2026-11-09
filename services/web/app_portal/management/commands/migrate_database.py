from __future__ import annotations

import os
from urllib.parse import unquote, urlsplit

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection
from psycopg2 import sql
from psycopg2.extensions import encrypt_password

# The lock local/start.py takes, so two migrations never run at once.
LOCK = 90000001
# The runtime role changes rows only. It cannot create, alter or drop tables or triggers, or roles.
# PUBLIC already allows it to connect and to use the public schema.
GRANTS = (
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}",
    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}",
    # Tables and sequences that later migrations create as this administrator.
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
    " GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}",
)


class Command(BaseCommand):
    help = (
        "Migrate as the database administrator, then create or update the restricted runtime "
        "role named in ISA9000_RUNTIME_DATABASE_URL and grant it row access."
    )

    def handle(self, *args, **options):
        runtime = urlsplit(os.environ["ISA9000_RUNTIME_DATABASE_URL"])
        name, password = unquote(runtime.username), unquote(runtime.password)
        role = sql.Identifier(name)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(%s)", [LOCK])
            try:
                call_command("migrate", interactive=False)
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [name])
                verb = "ALTER" if cursor.fetchone() else "CREATE"
                # Send a SCRAM verifier, never the password: the server logs failed statements
                # in full, and audit extensions log role statements.
                verifier = encrypt_password(
                    password, name, scope=connection.connection, algorithm="scram-sha-256"
                )
                # A new role is no superuser and cannot create roles or databases by default.
                # ALTER must not restate those: a non-superuser administrator may not.
                cursor.execute(
                    sql.SQL(verb + " ROLE {} LOGIN PASSWORD {}").format(role, sql.Literal(verifier))
                )
                for grant in GRANTS:
                    cursor.execute(sql.SQL(grant).format(role))
            finally:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK])
        self.stdout.write(f"Migrated; the runtime role {name} is ready.")
