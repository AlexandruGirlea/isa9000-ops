from __future__ import annotations

import ctypes
import json
import os
import sys
from uuid import UUID

from azure.identity import DefaultAzureCredential
from azure.servicebus import AutoLockRenewer, ServiceBusClient, ServiceBusReceiveMode
from opentelemetry import trace

from shared import payloads, telemetry
from worker.callbacks import Callbacks
from worker.runner import run_task

logger = telemetry.log(__name__)


def main() -> int:
    telemetry.setup("isa9000-worker")
    connection = os.environ.get("ISA9000_SERVICEBUS_CONNECTION_STRING")
    client = (
        ServiceBusClient.from_connection_string(connection) if connection else ServiceBusClient(
            os.environ["ISA9000_SERVICEBUS_NAMESPACE"], credential=DefaultAzureCredential()
        )
    )
    with client, client.get_queue_receiver(
        queue_name=os.environ.get("ISA9000_QUEUE", "tasks"),
        receive_mode=ServiceBusReceiveMode.PEEK_LOCK,
        prefetch_count=0,
    ) as receiver:
        messages = receiver.receive_messages(max_message_count=1, max_wait_time=30)
        if not messages:
            return 0
        message = messages[0]
        with telemetry.attach(message.application_properties or {}):
            with (
                trace.get_tracer(__name__).start_as_current_span("worker task"),
                AutoLockRenewer() as renewer,
            ):
                try:
                    run_id = str(UUID(str(message.message_id)))
                    task = payloads.decrypt(json.loads(str(message)), f"task:{run_id}")
                    if task.get("run_id") != run_id:
                        raise ValueError("Task identity does not match the message.")
                    callbacks = Callbacks(task)
                    fields = {
                        "actor_id": "worker", "run_id": task["run_id"],
                        "course_id": task["course_id"], "task": task["task"],
                        "group_id": task.get("group_id", ""),
                        "requested_by": task.get("requested_by", ""),
                        "operation_correlation_id": task["correlation_id"],
                    }
                    telemetry.audit("worker.received", **fields)
                    # The job scaler counts locked messages, so hold this one until the task ends.
                    renewer.register(receiver, message, max_lock_renewal_duration=7200)
                    accepted = callbacks.post("accept", {})["accepted"]
                    telemetry.audit(
                        "worker.accepted", outcome="success" if accepted else "ignored",
                        **fields,
                    )
                except Exception:
                    logger.error("Could not accept the task.")
                    receiver.abandon_message(message)
                    return 1
                try:
                    return run_task(task, callbacks) if accepted else 0
                finally:
                    receiver.complete_message(message)


if __name__ == "__main__":
    if sys.platform == "linux":
        # PR_SET_DUMPABLE=0: the engine runs as this user, so stop it reading this
        # process's environment (identity header, callback and payload keys) or memory.
        ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)
    try:
        raise SystemExit(main())
    finally:
        telemetry.flush()
