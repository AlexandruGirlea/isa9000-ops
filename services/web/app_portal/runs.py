from __future__ import annotations

import json
import os

from azure.identity import DefaultAzureCredential
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from core.audit import record
from django.conf import settings
from django.contrib.admin.models import ADDITION
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from app_portal.models import Course, Run
from shared import payloads, telemetry

STEPS = (
    ("check", "Checking your sources"),
    ("read", "Reading the material"),
    ("plan", "Planning the podcast"),
    ("write", "Writing and fact-checking episodes"),
    ("review", "Reviewing and polishing episodes"),
    ("record", "Recording the audio"),
    ("done", "Ready to listen"),
)


def dispatch(run):
    body = {
        "run_id": str(run.id),
        "task": run.task,
        "course_id": str(run.course_id),
        "correlation_id": run.correlation_id,
        "requested_by": run.requested_by.username,
        "group_id": str(run.course.group_id),
        "request": run.request,
        "title": run.course.title,
        "description": run.course.description,
        "sources": run.sources,
        "follow_links": run.follow_links,
        "generation_settings": run.generation_settings,
        "text_only": run.text_only,
        "links": {
            "media": f"/media/{run.course_id}/",
            "static": "/static/isa9000/",
            "home": f"/libraries/{run.course.group_id}/",
            "library": str(run.course.group),
        },
        "callback": f"{settings.CALLBACK_BASE_URL}/internal/runs/{run.id}",
    }
    if run.task in {"record", "render"}:
        body["manifest"] = run.course.manifest
    try:
        body = payloads.encrypt(body, f"task:{run.id}")
        connection = os.environ.get("ISA9000_SERVICEBUS_CONNECTION_STRING")
        credential = None if connection else DefaultAzureCredential()
        client = (
            ServiceBusClient.from_connection_string(connection)
            if connection
            else (ServiceBusClient(os.environ["ISA9000_SERVICEBUS_NAMESPACE"], credential))
        )
        try:
            queue = os.environ.get("ISA9000_QUEUE", "tasks")
            with client, client.get_queue_sender(queue) as sender:
                sender.send_messages(
                    ServiceBusMessage(
                        json.dumps(body),
                        message_id=str(run.id),
                        application_properties=telemetry.inject(),
                    )
                )
                telemetry.audit(
                    "run.dispatched", actor_id=run.requested_by.username,
                    run_id=str(run.id), course_id=str(run.course_id),
                    group_id=str(run.course.group_id), task=run.task,
                    operation_correlation_id=run.correlation_id,
                )
        finally:
            if credential:
                credential.close()
    except Exception:
        telemetry.audit(
            "run.dispatched", outcome="failed", actor_id=run.requested_by.username,
            run_id=str(run.id), course_id=str(run.course_id), task=run.task,
            operation_correlation_id=run.correlation_id,
        )
        Run.objects.filter(id=run.id, status="queued").update(
            status="failed",
            finished=timezone.now(),
            error_source="worker",
            error="The run could not be queued. Please retry.",
        )
        telemetry.log(__name__).error(
            "Task dispatch failed",
            extra={
                "run_id": str(run.id),
                "course_id": str(run.course_id),
                "task": run.task,
                "group_id": str(run.course.group_id),
                "enduser.id": run.requested_by.username,
                "state": "failed",
            },
        )


def new_run(
    course, user, task, request="", sources=None, follow_links=False, run_id=None,
    generation_settings=None, text_only=False,
):
    get_user_model().objects.select_for_update().get(pk=user.pk)
    Course.objects.select_for_update().get(pk=course.pk)
    if course.runs.filter(status__in=["queued", "running"]).exists():
        raise ValueError("This podcast already has a run in progress.")
    if Run.objects.filter(requested_by=user, created__date=timezone.now().date()).count() >= (
        settings.DAILY_RUNS_PER_USER
    ):
        raise ValueError("Your daily run limit has been reached.")
    steps = (
        STEPS
        if task == "create"
        else (
            (("render", "Updating the podcast page"), ("done", "Ready to listen"))
            if task == "render"
            else STEPS[-2:]
        )
    )
    if task == "create" and text_only:
        steps = (*STEPS[:-2], ("done", "Transcripts ready for your review"))
    run = Run.objects.create(
        **({"id": run_id} if run_id else {}),
        course=course,
        requested_by=user,
        task=task,
        request=request,
        sources=sources or [],
        correlation_id=telemetry.correlation_id(),
        follow_links=follow_links,
        generation_settings=(
            course.generation_settings if generation_settings is None else generation_settings
        ),
        text_only=text_only if task == "create" else False,
        steps=[
            {"key": key, "label": label, "status": "pending", "detail": ""} for key, label in steps
        ],
    )
    record(
        user, "run.requested", run, flag=ADDITION, run_id=str(run.id), course_id=str(course.id),
        group_id=str(course.group_id), task=task,
    )
    transaction.on_commit(lambda: dispatch(run))
    return run
