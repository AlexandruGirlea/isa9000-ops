from __future__ import annotations

import os
import sys

# The Azure stack sets this to false: its web role cannot change the schema (migrate_database does).
if sys.argv[1] == "gunicorn" and os.environ.get("ISA9000_MIGRATE_ON_START", "true") == "true":
    import django
    from django.core.management import call_command
    from django.db import connection

    django.setup()
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(90000001)")
        try:
            call_command("migrate", interactive=False)
        finally:
            cursor.execute("SELECT pg_advisory_unlock(90000001)")
    connection.close()

os.execvp(sys.argv[1], sys.argv[1:])
