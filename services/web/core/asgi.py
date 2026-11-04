from __future__ import annotations

import os

from django.core.asgi import get_asgi_application

from shared import telemetry

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
telemetry.setup("isa9000-web")
application = get_asgi_application()
