from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime

from opentelemetry import context, trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_FIELDS = (
    "correlation_id", "run_id", "course_id", "group_id", "task", "stage", "state",
    "current", "total", "enduser.id",
    "event", "event_id", "event_time", "action", "outcome", "actor_id", "target_type",
    "target_id", "changed_fields", "requested_by", "route", "method", "status_code",
    "operation_correlation_id",
)
_PROPAGATOR = TraceContextTextMapPropagator()
_setup_pids: set[int] = set()


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id() or getattr(record, "correlation_id", "")
        for field in _FIELDS:
            if not hasattr(record, field):
                setattr(record, field, "")
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **{field: getattr(record, field, "") for field in _FIELDS},
        }
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def setup(service: str) -> None:
    if os.getpid() in _setup_pids:
        return

    from opentelemetry.sdk.resources import Resource

    resource = Resource.create({"service.name": service})
    if service == "isa9000-web":
        exclusions = os.environ.get("OTEL_PYTHON_DJANGO_EXCLUDED_URLS", "")
        os.environ["OTEL_PYTHON_DJANGO_EXCLUDED_URLS"] = ",".join(
            item for item in (exclusions, r"/auth/login/aad/callback(?:\?|$)") if item
        )
    if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        from azure.identity import DefaultAzureCredential
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(
            resource=resource,
            credential=DefaultAzureCredential(),
            sampling_ratio=1.0,
            instrumentation_options={
                "django": {"enabled": service == "isa9000-web"},
                "psycopg2": {"enabled": service != "isa9000-worker"},
            },
        )
    else:
        from azure.core.settings import settings
        from opentelemetry.instrumentation.django import DjangoInstrumentor
        from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        from opentelemetry.sdk.trace import TracerProvider

        trace.set_tracer_provider(TracerProvider(resource=resource))
        if service == "isa9000-web":
            DjangoInstrumentor().instrument()
        if service != "isa9000-worker":
            Psycopg2Instrumentor().instrument()
        RequestsInstrumentor().instrument()
        settings.tracing_implementation = "opentelemetry"

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logging.getLogger().addHandler(handler)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in root.handlers:
        handler.addFilter(_ContextFilter())
    # The exporter logs each upload at INFO, and exporting those logs never ends.
    logging.getLogger("azure").setLevel(logging.WARNING)
    _setup_pids.add(os.getpid())


def correlation_id() -> str:
    span = trace.get_current_span().get_span_context()
    return f"{span.trace_id:032x}" if span.is_valid else ""


def inject() -> dict[str, str]:
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return {"traceparent": carrier["traceparent"]} if "traceparent" in carrier else {}


@contextmanager
def attach(properties: Mapping[str | bytes, str | bytes]) -> Iterator[None]:
    carrier = {
        (key.decode() if isinstance(key, bytes) else key).lower():
        value.decode() if isinstance(value, bytes) else value
        for key, value in properties.items()
        if isinstance(value, (str, bytes))
    }
    if "traceparent" not in carrier and "diagnostic-id" in carrier:
        carrier["traceparent"] = carrier["diagnostic-id"]
    token = context.attach(_PROPAGATOR.extract(carrier))
    try:
        yield
    finally:
        context.detach(token)


def log(name: str) -> logging.Logger:
    return logging.getLogger(name)


def audit(
    action: str, *, outcome: str = "success", actor_id: str = "", target_type: str = "",
    target_id: str = "", changed_fields=(), run_id: str = "", course_id: str = "",
    group_id: str = "", task: str = "", state: str = "", requested_by: str = "",
    route: str = "", method: str = "", status_code: int = 0,
    operation_correlation_id: str = "",
) -> None:
    log("isa9000.audit").info(
        "Audit event",
        extra={
            "event": "audit", "event_id": str(uuid.uuid4()),
            "event_time": datetime.now(UTC).isoformat(),
            "action": action, "outcome": outcome, "actor_id": str(actor_id),
            "target_type": target_type, "target_id": str(target_id),
            "changed_fields": json.dumps(sorted(changed_fields)),
            "run_id": str(run_id), "course_id": str(course_id), "group_id": str(group_id),
            "task": task, "state": state, "requested_by": str(requested_by),
            "route": route, "method": method, "status_code": status_code,
            "correlation_id": correlation_id(),
            "operation_correlation_id": operation_correlation_id or correlation_id(),
            "enduser.id": str(actor_id) if actor_id not in {"worker", "watchdog"} else "",
        },
    )


def flush() -> None:
    from opentelemetry._logs import get_logger_provider

    # force_flush loops while new records keep arriving; shutdown stops accepting them.
    for provider in (get_logger_provider(), trace.get_tracer_provider()):
        if shutdown := getattr(provider, "shutdown", None):
            shutdown()
