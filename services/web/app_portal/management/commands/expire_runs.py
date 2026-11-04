from __future__ import annotations

from datetime import timedelta

from django.contrib.sessions.backends.db import SessionStore
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from app_portal.models import Run
from shared import telemetry


class Command(BaseCommand):
    def handle(self, *args, **options):
        telemetry.setup("isa9000-watchdog")
        SessionStore.clear_expired()
        now = timezone.now()
        count = 0
        for status, field, minutes, error in (
            ("queued", "created", 15, "No worker picked up this request within 15 minutes."),
            ("running", "last_seen", 5, "The worker has not reported progress for 5 minutes."),
        ):
            with transaction.atomic():
                runs = Run.objects.select_for_update().filter(
                    status=status,
                    **{
                        f"{field}__lt": now - timedelta(minutes=minutes),
                    },
                )
                for run in runs:
                    run.status, run.error, run.error_source = "failed", error, "worker"
                    run.finished = now
                    run.save(update_fields=["status", "error", "error_source", "finished"])
                    count += 1
                    fields = {
                        "actor_id": "watchdog", "run_id": str(run.id),
                        "course_id": str(run.course_id), "task": run.task, "state": "failed",
                        "operation_correlation_id": run.correlation_id,
                    }
                    transaction.on_commit(
                        lambda fields=fields: telemetry.audit("run.expired", **fields)
                    )
                    telemetry.log(__name__).error(
                        "Request expired",
                        extra={
                            "state": "failed",
                            "run_id": str(run.id),
                            "course_id": str(run.course_id),
                            "task": run.task,
                            "correlation_id": run.correlation_id,
                        },
                    )
        self.stdout.write(f"Expired {count} requests.")
        telemetry.flush()
