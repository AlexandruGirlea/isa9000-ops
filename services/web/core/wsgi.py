from __future__ import annotations

import os

from django.core.wsgi import get_wsgi_application

from shared import telemetry

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
telemetry.setup("isa9000-web")
application = get_wsgi_application()
