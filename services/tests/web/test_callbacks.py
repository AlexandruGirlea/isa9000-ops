from __future__ import annotations

import json
import uuid
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from app_portal.models import Run
from django.core.management import call_command
from django.test import Client
from django.utils import timezone

from shared import payloads

from .helpers import PortalCase


class CallbackTests(PortalCase):
    def test_secret_and_unknown_run(self):
        path = f"/internal/runs/{self.run.id}/accept"
        for secret in ("", "Bearer wrong", "Bearer λ"):
            with self.subTest(secret=secret):
                self.assertEqual(
                    self.client.post(
                        path, "{}", content_type="application/json", HTTP_AUTHORIZATION=secret
                    ).status_code,
                    401,
                )
        missing = uuid.uuid4()
        response = self.client.post(
            f"/internal/runs/{missing}/accept",
            json.dumps(payloads.encrypt({}, f"callback:{missing}:accept")),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer tests-only-callback",
        )
        self.assertEqual(response.status_code, 404)

    def test_callbacks_are_csrf_exempt(self):
        response = Client(enforce_csrf_checks=True).post(
            f"/internal/runs/{self.run.id}/accept",
            json.dumps(payloads.encrypt({}, f"callback:{self.run.id}:accept")),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer tests-only-callback",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            payloads.decrypt(response.json(), f"reply:{self.run.id}:accept"), {"accepted": True},
        )

    def test_accept_once_and_reject_other_states(self):
        self.assertEqual(self.callback("accept").json(), {"accepted": True})
        for state in ("running", "ready", "needs_review", "failed", "stopped"):
            with self.subTest(state=state):
                Run.objects.filter(pk=self.run.pk).update(status=state)
                self.assertEqual(self.callback("accept").json(), {"accepted": False})
                self.run.refresh_from_db()
                self.assertEqual(self.run.status, state)

    def test_progress_and_heartbeat_update_liveness_and_cancel(self):
        self.callback("accept")
        old = timezone.now() - timedelta(minutes=3)
        Run.objects.filter(pk=self.run.pk).update(
            last_seen=old,
            cancel_requested=True,
            steps=[{"key": "write", "label": "Write", "status": "pending", "detail": ""}],
        )
        self.assertEqual(self.callback("progress", {"stage": "heartbeat"}).json(), {"cancel": True})
        self.assertEqual(
            self.callback(
                "progress", {"stage": "verify", "message": "Writing", "current": 1, "total": 2}
            ).status_code,
            200,
        )
        self.run.refresh_from_db()
        self.assertGreater(self.run.last_seen, old)
        self.assertEqual(self.run.log, ["Writing"])
        self.assertEqual(self.run.steps[0]["detail"], "Episode 1 of 2")
        self.assertEqual(self.run.steps[0]["status"], "active")

    def test_progress_requires_running(self):
        for state in ("queued", "ready", "needs_review", "failed", "stopped"):
            Run.objects.filter(pk=self.run.pk).update(status=state)
            with self.subTest(state=state):
                self.assertEqual(self.callback("progress", {"stage": "heartbeat"}).status_code, 409)

    def test_ready_saves_result_html_manifest_and_course_atomically(self):
        self.callback("accept")
        result = {
            "state": "ready",
            "manifest": {**self.manifest, "title": "Updated"},
            "html": "<!doctype html>Updated",
            "error": "",
            "error_source": "",
        }
        self.assertEqual(self.callback("result", result).json(), {"ok": True})
        self.course.refresh_from_db()
        self.run.refresh_from_db()
        self.assertEqual(self.course.html, result["html"])
        self.assertEqual(self.course.title, "Updated")
        self.assertEqual(self.run.result, result)
        self.assertEqual(self.run.status, "ready")
        self.assertIsNotNone(self.run.finished)

    def test_result_idempotence_survives_later_course_update(self):
        self.callback("accept")
        result = {"state": "ready", "manifest": self.manifest, "html": "First HTML"}
        self.callback("result", result)
        self.run.refresh_from_db()
        finished = self.run.finished
        later = Run.objects.create(course=self.course, requested_by=self.user, task="render")
        self.callback("accept", run=later)
        self.callback("result", {**result, "html": "New HTML"}, run=later)
        self.assertEqual(self.callback("result", result).status_code, 200)
        self.assertEqual(self.callback("result", {**result, "html": "Different"}).status_code, 409)
        self.course.refresh_from_db()
        self.run.refresh_from_db()
        self.assertEqual(self.course.html, "New HTML")
        self.assertEqual(self.run.finished, finished)

    def test_needs_review_has_no_html_and_record_makes_ready(self):
        self.course.status, self.course.html = "draft", ""
        self.course.save()
        self.callback("accept")
        self.assertEqual(
            self.callback(
                "result", {"state": "needs_review", "manifest": self.manifest}
            ).status_code,
            200,
        )
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, "needs_review")
        self.assertEqual(self.course.html, "")
        run = Run.objects.create(course=self.course, requested_by=self.user, task="record")
        self.callback("accept", run=run)
        self.callback(
            "result", {"state": "ready", "manifest": self.manifest, "html": "Recorded"}, run=run
        )
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, "ready")

    def test_failed_and_stopped_preserve_course_status_and_html_but_save_manifest(self):
        for status in ("draft", "needs_review", "ready"):
            for state in ("failed", "stopped"):
                with self.subTest(status=status, state=state):
                    self.course.status = status
                    self.course.save()
                    run = Run.objects.create(
                        course=self.course, requested_by=self.user, task="create"
                    )
                    self.callback("accept", run=run)
                    result = {
                        "state": state,
                        "manifest": {"title": state, "episodes": []},
                        "error": "Multiline\nerror",
                        "error_source": "isa9000",
                    }
                    with patch("app_portal.callbacks.telemetry.log") as logger:
                        self.assertEqual(self.callback("result", result, run=run).status_code, 200)
                    self.course.refresh_from_db()
                    self.assertEqual(self.course.status, status)
                    self.assertEqual(self.course.html, "<!doctype html>Course")
                    self.assertEqual(self.course.manifest, result["manifest"])
                    if state == "failed":
                        self.assertEqual(
                            logger.return_value.error.call_args.kwargs["extra"]["state"], "failed"
                        )

    def test_render_does_not_change_course_state(self):
        self.course.status = "needs_review"
        self.course.save()
        self.run.task = "render"
        self.run.save()
        self.callback("accept")
        self.callback("result", {"state": "ready", "html": "Rendered", "manifest": self.manifest})
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, "needs_review")

    def test_invalid_results_and_result_before_accept_are_refused(self):
        self.assertEqual(self.callback("result", {"state": "failed"}).status_code, 409)
        self.callback("accept")
        for result in (
            {"state": "bogus"},
            {"state": "ready"},
            {"state": "failed", "html": "bad"},
            {"state": "failed", "manifest": []},
            {"state": "failed", "error": []},
        ):
            with self.subTest(result=result):
                self.assertEqual(self.callback("result", result).status_code, 409)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "running")

    def test_expire_runs_only_expires_stale_active_runs_once(self):
        now = timezone.now()
        Run.objects.filter(pk=self.run.pk).update(created=now - timedelta(minutes=16))
        stale = Run.objects.create(
            course=self.course,
            requested_by=self.user,
            task="record",
            status="running",
            last_seen=now - timedelta(minutes=6),
        )
        fresh = Run.objects.create(
            course=self.course,
            requested_by=self.user,
            task="render",
            status="running",
            last_seen=now - timedelta(minutes=4),
        )
        done = Run.objects.create(
            course=self.course,
            requested_by=self.user,
            task="create",
            status="ready",
            last_seen=now - timedelta(days=1),
        )
        with (
            patch("app_portal.management.commands.expire_runs.telemetry.setup"),
            patch("app_portal.management.commands.expire_runs.telemetry.log") as logger,
        ):
            output = StringIO()
            call_command("expire_runs", stdout=output)
            call_command("expire_runs", stdout=output)
        self.assertEqual(logger.return_value.error.call_count, 2)
        self.assertIn("Expired 2", output.getvalue())
        self.assertIn("Expired 0", output.getvalue())
        for run, expected in (
            (self.run, "failed"),
            (stale, "failed"),
            (fresh, "running"),
            (done, "ready"),
        ):
            run.refresh_from_db()
            self.assertEqual(run.status, expected)
        self.assertEqual(self.callback("accept").json(), {"accepted": False})

    def test_invalid_json_and_unknown_callback_action(self):
        response = self.client.post(
            f"/internal/runs/{self.run.id}/result",
            "not-json",
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer tests-only-callback",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.callback("unknown").status_code, 404)
        response = self.client.post(
            f"/internal/runs/{self.run.id}/result",
            json.dumps([]),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer tests-only-callback",
        )
        self.assertEqual(response.status_code, 409)
