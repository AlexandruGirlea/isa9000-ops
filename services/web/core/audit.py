from __future__ import annotations

from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.db import transaction

from shared import telemetry


def record(user, action, obj, *, flag=CHANGE, changed_fields=(), **context):
    fields = {
        "actor_id": user.username,
        "target_type": obj._meta.label_lower,
        "target_id": str(obj.pk),
        "changed_fields": tuple(changed_fields),
        "operation_correlation_id": telemetry.correlation_id(),
        **context,
    }
    telemetry.audit(action, outcome="pending_commit", **fields)
    message = {
        ADDITION: {"added": {"name": obj._meta.verbose_name, "object": str(obj)}},
        CHANGE: {"changed": {"fields": list(changed_fields) or [action]}},
        DELETION: {"deleted": {"name": obj._meta.verbose_name, "object": str(obj)}},
    }[flag]
    LogEntry.objects.log_actions(
        user_id=user.pk, queryset=[obj], action_flag=flag,
        change_message=[message, {"audit": {
            "action": action, "correlation_id": telemetry.correlation_id(), **fields,
        }}],
    )
    transaction.on_commit(lambda: telemetry.audit(action, **fields))
