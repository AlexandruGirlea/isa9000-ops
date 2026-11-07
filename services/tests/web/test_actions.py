from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app_portal.models import Course, Run
from app_portal.runs import dispatch, new_run
from azure.core.exceptions import ResourceNotFoundError
from django.contrib.admin.models import LogEntry
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import override_settings
from opentelemetry import trace

from shared import payloads

from .helpers import SALES, PortalCase


class ActionTests(PortalCase):
    def test_create_uploads_and_task_contract(self):
        file = SimpleUploadedFile("handbook.md", b"A source document")
        context = trace.SpanContext(
            trace_id=int("b" * 32, 16), span_id=19, is_remote=False, trace_flags=trace.TraceFlags(1)
        )
        with (
            trace.use_span(trace.NonRecordingSpan(context)),
            patch("app_portal.runs.dispatch") as send,
            patch("app_portal.views.storage.save_upload") as upload,
        ):
            response = self.client.post(
                f"/libraries/{SALES}/new/", {"request": "Teach onboarding", "title": "x" * 81},
                **self.headers,
            )
            self.assertContains(response, "errorlist")
            upload.assert_not_called()
            send.assert_not_called()
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    f"/libraries/{SALES}/new/",
                    {
                        "request": "Teach onboarding", "title": "  Onboarding essentials  ",
                        "uploads": file, "follow_links": "on",
                    },
                    **self.headers,
                )
        self.assertEqual(response.status_code, 302)
        run = Run.objects.exclude(pk=self.run.pk).get()
        self.assertEqual(run.request, "Teach onboarding")
        self.assertTrue(run.follow_links)
        self.assertEqual(run.correlation_id, "b" * 32)
        self.assertEqual(run.sources, [f"uploads/{run.id}/1-handbook.md"])
        self.assertEqual(run.course.title, "Onboarding essentials")
        upload.assert_called_once()
        send.assert_called_once()

    @override_settings(DAILY_RUNS_PER_USER=1)
    def test_daily_limit_rolls_back_new_course(self):
        response = self.client.post(
            f"/libraries/{SALES}/new/",
            {"request": "Another course", "title": "Another course"}, **self.headers,
        )
        self.assertContains(response, "daily run limit")
        self.assertEqual(Course.objects.count(), 1)
        self.assertEqual(Run.objects.count(), 1)

    def test_cannot_start_second_active_run(self):
        with transaction.atomic(), self.assertRaisesMessage(ValueError, "already has"):
            new_run(self.course, self.user, "record")
        response = self.client.post(
            f"/courses/{self.course.id}/manage/",
            {"action": "rename", "title": "Renamed"}, **self.headers,
        )
        self.assertEqual(response.status_code, 409)
        self.course.refresh_from_db()
        self.assertEqual(self.course.title, "Sales course")

    def test_stop_queued_and_running(self):
        response = self.client.post(f"/runs/{self.run.id}/", {"action": "stop"}, **self.headers)
        self.assertEqual(response.status_code, 302)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "stopped")
        self.assertIsNotNone(self.run.finished)
        self.assertEqual(self.callback("accept").json(), {"accepted": False})
        other = Run.objects.create(
            course=self.course, requested_by=self.user, task="record", status="running"
        )
        self.client.post(f"/runs/{other.id}/", {"action": "stop"}, **self.headers)
        other.refresh_from_db()
        self.assertTrue(other.cancel_requested)
        self.assertEqual(other.status, "running")
        self.assertEqual(
            self.callback("progress", {"stage": "heartbeat"}, run=other).json(), {"cancel": True}
        )
        self.callback("result", {"state": "stopped"}, run=other)
        other.refresh_from_db()
        self.assertEqual(other.status, "stopped")

    def test_retry_preserves_request_sources_and_follow_links(self):
        self.course.manifest = {}
        self.course.save()
        self.run.status, self.run.follow_links = "failed", True
        self.run.sources = [f"uploads/{self.run.id}/original.pdf"]
        self.run.save()
        with patch("app_portal.views.storage.read_json", side_effect=ResourceNotFoundError):
            response = self.client.post(
                f"/runs/{self.run.id}/", {"action": "retry"}, **self.headers
            )
        self.assertEqual(response.status_code, 302)
        retry = Run.objects.exclude(pk=self.run.pk).get()
        self.assertEqual(retry.task, "create")
        self.assertEqual(retry.sources, self.run.sources)
        self.assertEqual(retry.request, self.run.request)
        self.assertTrue(retry.follow_links)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "failed")

    def in_review(self):
        self.run.status = self.course.status = "needs_review"
        self.run.save()
        self.course.save()

    def post_transcript(self, **body):
        return self.client.post(
            f"/courses/{self.course.id}/episodes/1/transcript/", json.dumps(body),
            content_type="application/json", **self.headers,
        )

    def test_transcript_edit_clears_the_audio_and_never_logs_the_text(self):
        self.course.manifest["review"] = {"score": 7.5, "approved": False, "fact_checked": True}
        self.in_review()
        revision = self.course.updated.isoformat()
        text = "Approved wording. <script>Literal text</script>"
        with (
            patch("app_portal.transcripts.storage.read_text", return_value="Original text"),
            patch("app_portal.transcripts.storage.save_upload") as upload,
            patch("core.audit.telemetry.audit") as audit,
        ):
            valid = {"text": text, "revision": revision}
            for changes, code in (
                ({"text": " \n"}, 400), ({"text": "x" * 100001}, 413), ({"revision": "stale"}, 409),
                ({"padding": "x" * (1024 * 1024)}, 413),
            ):
                with self.subTest(changes=list(changes)):
                    self.assertEqual(self.post_transcript(**valid | changes).status_code, code)
            upload.assert_not_called()
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.post_transcript(**valid).status_code, 200)
        self.course.refresh_from_db()
        episode = self.course.manifest["episodes"][0]
        self.assertIsNone(episode["audio"])
        self.assertEqual(self.course.manifest["episodes"][1], self.manifest["episodes"][1])
        self.assertFalse(self.course.manifest["review"]["fact_checked"])
        upload.assert_called_once_with(
            text.encode(), f"courses/{self.course.id}/{episode['transcript']}"
        )
        self.assertTrue(LogEntry.objects.filter(
            user=self.user, change_message__contains="transcript.updated"
        ).exists())
        self.assertNotIn(text, str(audit.mock_calls))
        self.assertFalse(LogEntry.objects.filter(change_message__contains=text).exists())

    def test_editing_a_published_transcript_needs_unpublishing(self):
        self.run.status = "needs_review"
        self.run.save()
        body = {"text": "New wording", "revision": self.course.updated.isoformat()}
        with (
            patch("app_portal.transcripts.storage.read_text", return_value="Original text"),
            patch("app_portal.transcripts.storage.save_upload") as upload,
        ):
            self.assertEqual(self.post_transcript(**body).status_code, 409)
            upload.assert_not_called()
            self.assertEqual(self.post_transcript(**body, unpublish=True).status_code, 200)
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, "needs_review")
        self.assertEqual(self.course.html, "")

    def test_recording_needs_approval_of_the_current_version_and_runs_once(self):
        self.in_review()
        path = f"/courses/{self.course.id}/manage/"
        stale = self.course.updated.isoformat()
        self.course.save()
        revision = self.course.updated.isoformat()
        approve = {"action": "record", "approved": "on", "revision": revision}
        self.assertEqual(
            self.client.post(path, {**approve, "revision": stale}, **self.headers).status_code, 409,
        )
        self.assertContains(
            self.client.post(path, {"action": "record"}, **self.headers),
            "Confirm your approval before recording.",
        )
        self.assertEqual(Run.objects.count(), 1)
        self.assertEqual(self.client.post(path, approve, **self.headers).status_code, 302)
        self.assertEqual(Run.objects.exclude(pk=self.run.pk).get().task, "record")
        self.assertTrue(LogEntry.objects.filter(
            user=self.user, change_message__contains="course.review_approved"
        ).exists())
        self.assertEqual(self.client.post(path, approve, **self.headers).status_code, 409)
        self.assertEqual(Run.objects.count(), 2)

    def test_delete_episode_updates_blob_manifest_and_queues_render(self):
        self.run.status = "ready"
        self.run.save()
        with (
            patch("app_portal.views.storage.delete_blobs") as delete,
            patch("app_portal.views.storage.write_json") as write,
        ):
            response = self.client.post(
                f"/courses/{self.course.id}/manage/",
                {"action": "delete_episode", "episode": 1}, **self.headers,
            )
            deleted = list(delete.call_args.args[0])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(deleted, [
            f"courses/{self.course.id}/01 - First.mp3", f"courses/{self.course.id}/01 - First.txt",
        ])
        self.assertEqual(len(write.call_args.args[1]["episodes"]), 1)
        self.assertEqual(Run.objects.exclude(pk=self.run.pk).get().task, "render")

    def test_renaming_a_ready_podcast_republishes_it_and_escapes_the_title(self):
        self.run.status = "ready"
        self.run.save()
        path = f"/courses/{self.course.id}/manage/"
        with patch("app_portal.views.storage.write_json") as write:
            response = self.client.post(
                path, {"action": "rename", "title": "Ready <podcast>"}, **self.headers,
            )
        run = Run.objects.get(status="queued")
        self.assertEqual(run.task, "render")
        self.assertEqual(response.url, f"/runs/{run.id}/")
        self.assertEqual(write.call_args.args[1]["title"], "Ready <podcast>")
        self.assertNotContains(self.client.get(path, **self.headers), "Ready <podcast>")

    def test_delete_last_episode_deletes_course_and_blobs(self):
        self.run.status = "ready"
        self.run.save()
        self.course.manifest["episodes"] = self.course.manifest["episodes"][:1]
        self.course.save()
        with patch("app_portal.views.storage.delete_prefix") as delete:
            response = self.client.post(
                f"/courses/{self.course.id}/manage/",
                {"action": "delete_episode", "episode": 1}, **self.headers,
            )
        self.assertEqual(response.url, f"/libraries/{SALES}/")
        delete.assert_called_once_with(f"courses/{self.course.id}")
        self.assertFalse(Course.objects.exists())

    def test_delete_course_and_refuse_active_delete(self):
        path = f"/courses/{self.course.id}/manage/"
        with patch("app_portal.views.storage.delete_prefix") as delete:
            self.assertEqual(
                self.client.post(path, {"action": "delete_course"}, **self.headers).status_code, 409
            )
            delete.assert_not_called()
            self.run.status = "ready"
            self.run.save()
            self.assertEqual(
                self.client.post(path, {"action": "delete_course"}, **self.headers).status_code, 302
            )
            delete.assert_called_once()
        self.assertFalse(Course.objects.exists())

    def test_queue_message_body_and_trace_match_contract(self):
        self.run.follow_links = True
        self.run.task = "record"
        self.run.save()
        factory = MagicMock()
        sender = factory.from_connection_string.return_value.get_queue_sender.return_value
        with (
            patch.dict("os.environ", {"ISA9000_SERVICEBUS_CONNECTION_STRING": "local-test"}),
            patch("app_portal.runs.ServiceBusClient", factory),
            patch("app_portal.runs.telemetry.inject", return_value={"traceparent": "parent"}),
        ):
            dispatch(self.run)
        message = sender.__enter__.return_value.send_messages.call_args.args[0]
        envelope = json.loads(b"".join(message.body))
        self.assertEqual(set(envelope), {"v", "payload"})
        body = payloads.decrypt(envelope, f"task:{self.run.id}")
        self.assertEqual(body["run_id"], str(self.run.id))
        self.assertTrue(body["follow_links"])
        self.assertEqual(body["title"], self.course.title)
        self.assertEqual(body["manifest"], self.course.manifest)
        self.assertEqual(body["description"], self.course.description)
        self.assertEqual(body["links"]["home"], f"/libraries/{SALES}/")
        self.assertEqual(body["callback"], f"http://testserver/internal/runs/{self.run.id}")
        self.assertEqual(message.message_id, str(self.run.id))
        self.assertEqual(message.application_properties, {"traceparent": "parent"})

    def test_dispatch_failure_is_a_failed_run_without_sensitive_logs(self):
        with (
            patch.dict("os.environ", {"ISA9000_SERVICEBUS_CONNECTION_STRING": "local-test"}),
            patch(
                "app_portal.runs.ServiceBusClient.from_connection_string", side_effect=RuntimeError
            ),
            patch("app_portal.runs.telemetry.log") as logger,
        ):
            dispatch(self.run)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "failed")
        self.assertEqual(logger.return_value.error.call_args.kwargs["extra"]["state"], "failed")
        self.assertNotIn(self.run.request, str(logger.mock_calls))
