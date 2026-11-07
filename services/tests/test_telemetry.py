from __future__ import annotations

import os
import subprocess
import sys

import pytest
from opentelemetry import trace

from shared import telemetry


@pytest.mark.parametrize("property_name", ["traceparent", b"traceparent", b"Diagnostic-Id"])
def test_trace_continuation_and_cleanup(property_name):
    parent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    value = parent.encode() if isinstance(property_name, bytes) else parent
    original = trace.get_current_span().get_span_context()
    with telemetry.attach({property_name: value}):
        assert telemetry.correlation_id() == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert telemetry.inject() == {"traceparent": parent}
    assert trace.get_current_span().get_span_context() == original


@pytest.mark.parametrize("azure", [False, True])
def test_worker_setup_does_not_load_django_settings(azure):
    script = '''
import os
import sys
from unittest.mock import patch
from shared import telemetry

os.environ["DJANGO_SETTINGS_MODULE"] = "core.settings"
for name in ("ISA9000_DJANGO_SECRET_KEY", "DATABASE_URL"):
    os.environ.pop(name, None)
if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
    with patch("azure.monitor.opentelemetry.configure_azure_monitor") as configure:
        telemetry.setup("isa9000-worker")
        options = configure.call_args.kwargs["instrumentation_options"]
        assert options["django"] == {"enabled": False}
        assert options["psycopg2"] == {"enabled": False}
else:
    telemetry.setup("isa9000-worker")
assert "core.settings" not in sys.modules
print("Worker telemetry ready without Django settings.")
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "APPLICATIONINSIGHTS_CONNECTION_STRING": "test" if azure else ""},
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Worker telemetry ready without Django settings." in result.stdout
