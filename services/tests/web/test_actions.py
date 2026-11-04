from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from app_portal.models import Course, Profile, Run
from app_portal.runs import dispatch, new_run
from azure.core.exceptions import ResourceNotFoundError
from core.dev import SALES
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import override_settings
from opentelemetry import trace

from shared import payloads

from .helpers import PortalCase


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
            for values in (
                {"title": ""}, {"title": " "}, {"title": "x" * 81},
                {"title": "Onboarding", "description": "x" * 10001},
            ):
                with self.subTest(invalid_fields=list(values)):
                    response = self.client.post(
                        f"/libraries/{SALES}/new/", {"request": "Teach onboarding", **values},
                        **self.headers,
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, "errorlist")
                    upload.assert_not_called()
                    send.assert_not_called()
                    self.assertEqual(Course.objects.count(), 1)
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    f"/libraries/{SALES}/new/",
                    {
                        "request": "Teach onboarding",
                        "title": "  Onboarding essentials  ",
                        "description": "Employee onboarding",
                        "uploads": file,
                        "follow_links": "on",
                    },
                    **self.headers,
                )
        self.assertEqual(response.status_code, 302)
        run = Run.objects.exclude(pk=self.run.pk).get()
        self.assertEqual(run.request, "Teach onboarding")
        self.assertTrue(run.follow_links)
        self.assertEqual(run.correlation_id, "b" * 32)
        self.assertEqual(run.sources, [f"uploads/{run.id}/1-handbook.md"])
        upload.assert_called_once()
        send.assert_called_once()
        self.assertEqual(run.course.status, "draft")
        self.assertEqual(run.course.title, "Onboarding essentials")
        self.assertEqual(run.course.description, "Employee onboarding")

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

    def test_retry_uses_record_with_saved_manifest(self):
        self.run.status = "failed"
        self.run.save()
        self.client.post(f"/runs/{self.run.id}/", {"action": "retry"}, **self.headers)
        self.assertEqual(Run.objects.exclude(pk=self.run.pk).get().task, "record")

    def test_retry_recovers_early_upload_after_worker_death(self):
        self.run.status = "failed"
        self.run.save()
        self.course.manifest = {}
        self.course.save()
        with patch("app_portal.views.storage.read_json", return_value=self.manifest):
            self.client.post(f"/runs/{self.run.id}/", {"action": "retry"}, **self.headers)
        self.course.refresh_from_db()
        self.assertEqual(self.course.manifest, self.manifest)
        self.assertEqual(Run.objects.exclude(pk=self.run.pk).get().task, "record")
        Run.objects.exclude(pk=self.run.pk).update(status="failed")
        self.course.status = "draft"
        self.course.manifest["review"] = {"approved": False, "score": 7.5}
        self.course.save()
        response = self.client.post(
            f"/runs/{self.run.id}/", {"action": "retry"}, **self.headers
        )
        self.assertEqual(response.url, f"/courses/{self.course.id}/manage/")
        self.assertEqual(Run.objects.count(), 2)
        self.course.refresh_from_db()
        self.assertEqual(self.course.status, "needs_review")

    def test_record_review_and_poll(self):
        self.run.status = "needs_review"
        self.run.save()
        self.course.status = "needs_review"
        owner = get_user_model().objects.create_user(username="different-creator")
        Profile.objects.create(user=owner, role="creator").groups.add(self.sales)
        self.course.created_by = owner
        self.course.manifest["review"] = {"score": 7.5, "approved": False, "fact_checked": True}
        self.course.save()
        path = f"/courses/{self.course.id}/manage/"
        transcript_path = f"/courses/{self.course.id}/episodes/1/transcript/"
        old_revision = self.course.updated.isoformat()
        text = "Approved wording for the sales team. <script>Literal text</script>"
        with (
            patch("app_portal.transcripts.storage.read_text", return_value="Original text") as read,
            patch("app_portal.transcripts.storage.save_upload") as upload,
            patch("app_portal.transcripts.storage.delete_blobs") as delete,
            patch("app_portal.transcripts.storage.delete_prefix") as delete_all,
            patch("app_portal.transcripts.storage.write_json") as write,
            patch("app_portal.runs.dispatch") as send,
            patch("core.audit.telemetry.audit") as audit,
        ):
            self.run.status = "running"
            self.run.save()
            response = self.client.post(
                transcript_path, json.dumps({"text": text, "revision": old_revision}),
                content_type="application/json", **self.headers,
            )
            self.assertEqual(response.status_code, 409)
            self.run.status = "needs_review"
            self.run.save()
            for changes, code in (
                ({"text": " \n\t"}, 400), ({"text": []}, 400), ({"text": "\ud800"}, 400),
                ({"text": "x" * 100001}, 413), ({"revision": "stale"}, 409),
                ({"padding": "x" * (1024 * 1024)}, 413),
            ):
                with self.subTest(invalid_fields=list(changes)):
                    response = self.client.post(
                        transcript_path,
                        json.dumps({"text": text, "revision": old_revision, **changes}),
                        content_type="application/json", **self.headers,
                    )
                    self.assertEqual(response.status_code, code)
            upload.assert_not_called()
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    transcript_path, json.dumps({"text": text, "revision": old_revision}),
                    content_type="application/json", **self.headers,
                )
            self.assertEqual(response.status_code, 200)
            self.course.refresh_from_db()
            episode = self.course.manifest["episodes"][0]
            self.assertNotEqual(episode["transcript"], self.manifest["episodes"][0]["transcript"])
            self.assertIsNone(episode["audio"])
            self.assertIsNone(episode["seconds"])
            self.assertTrue(episode["edited"])
            self.assertEqual(self.course.manifest["episodes"][1], self.manifest["episodes"][1])
            self.assertEqual(self.course.status, "needs_review")
            self.assertEqual(self.course.html, "")
            self.assertEqual(self.course.manifest["review"]["score"], 7.5)
            self.assertFalse(self.course.manifest["review"]["approved"])
            self.assertFalse(self.course.manifest["review"]["fact_checked"])
            self.assertTrue(self.course.manifest["review"]["edited"])
            self.assertNotEqual(response.json()["revision"], old_revision)
            upload.assert_called_once_with(
                text.encode(), f"courses/{self.course.id}/{episode['transcript']}"
            )
            self.assertTrue(LogEntry.objects.filter(
                user=self.user, change_message__contains="transcript.updated"
            ).exists())
            self.assertNotIn(text, str(audit.mock_calls))
            self.assertFalse(LogEntry.objects.filter(change_message__contains=text).exists())
            read.return_value = text
            published_text = text + " More guidance."
            self.course.status, self.course.html = "ready", "<!doctype html>Published"
            self.course.save()
            published_revision = self.course.updated.isoformat()
            upload.reset_mock()
            response = self.client.post(
                transcript_path,
                json.dumps({"text": published_text, "revision": published_revision}),
                content_type="application/json", **self.headers,
            )
            self.assertEqual(response.status_code, 409)
            response = self.client.post(
                transcript_path,
                json.dumps({
                    "text": published_text, "revision": published_revision, "unpublish": "true",
                }),
                content_type="application/json", **self.headers,
            )
            self.assertIn(response.status_code, (400, 409))
            upload.assert_not_called()
            response = self.client.post(
                transcript_path,
                json.dumps({
                    "text": published_text, "revision": published_revision, "unpublish": True,
                }),
                content_type="application/json", **self.headers,
            )
            self.assertEqual(response.status_code, 200)
            self.course.refresh_from_db()
            self.assertEqual(self.course.status, "needs_review")
            self.assertEqual(self.course.html, "")
            self.assertNotEqual(
                self.course.manifest["episodes"][0]["transcript"], episode["transcript"]
            )
            self.assertEqual(Run.objects.count(), 1)
            delete.assert_not_called()
            delete_all.assert_not_called()
            write.assert_not_called()
            send.assert_not_called()
        response = self.client.post(
            path, {"action": "record", "approved": "on", "revision": old_revision}, **self.headers
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Run.objects.count(), 1)
        with patch("app_portal.views.storage.write_json") as write:
            for title in (" ", "x" * 81):
                response = self.client.post(
                    path, {"action": "rename", "title": title}, **self.headers
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "errorlist")
            write.assert_not_called()
            response = self.client.post(
                path, {"action": "rename", "title": "Reviewed title",
                       "description": "Reviewed description"}, **self.headers,
            )
            self.assertEqual(response.url, path)
            self.course.refresh_from_db()
            self.assertEqual(self.course.title, "Reviewed title")
            self.assertEqual(self.course.description, "Reviewed description")
            self.assertEqual(self.course.manifest["title"], "Reviewed title")
            self.assertEqual(self.course.manifest["description"], "Reviewed description")
            write.assert_called_once_with(
                f"courses/{self.course.id}/podcast.json", self.course.manifest
            )
            self.assertEqual(Run.objects.count(), 1)
        response = self.client.post(path, {"action": "record"}, **self.headers)
        self.assertContains(response, "Confirm your approval before recording.")
        self.assertEqual(Run.objects.count(), 1)
        response = self.client.get(f"/runs/{self.run.id}/", **self.headers)
        self.assertContains(response, "Approve &amp; record audio")
        response = self.client.post(
            path, {"action": "record", "approved": "on",
                   "revision": self.course.updated.isoformat()}, **self.headers,
        )
        self.assertEqual(response.status_code, 302)
        run = Run.objects.exclude(pk=self.run.pk).get()
        self.assertEqual(run.task, "record")
        self.assertTrue(LogEntry.objects.filter(
            user=self.user, change_message__contains="course.review_approved"
        ).exists())
        response = self.client.get(f"/runs/{self.run.id}/", **self.headers)
        self.assertContains(response, "A newer run is in progress")
        self.assertNotContains(response, "Approve &amp; record audio")
        response = self.client.post(
            path, {"action": "record", "approved": "on",
                   "revision": self.course.updated.isoformat()}, **self.headers,
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Run.objects.count(), 2)
        response = self.client.get(
            f"/runs/{run.id}/", HTTP_ACCEPT="application/json", **self.headers
        )
        self.assertEqual(response.json()["status"], "queued")
        self.assertEqual(response["Cache-Control"], "private, no-store")

    def test_delete_episode_updates_blob_manifest_and_queues_render(self):
        self.run.status = "ready"
        self.run.save()
        with (
            patch("app_portal.views.storage.delete_blobs") as delete,
            patch("app_portal.views.storage.write_json") as write,
        ):
            response = self.client.post(
                f"/courses/{self.course.id}/manage/",
                {
                    "action": "delete_episode",
                    "episode": 1,
                },
                **self.headers,
            )
            deleted = list(delete.call_args.args[0])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            deleted,
            [
                f"courses/{self.course.id}/01 - First.mp3",
                f"courses/{self.course.id}/01 - First.txt",
            ],
        )
        manifest = write.call_args.args[1]
        self.assertEqual(len(manifest["episodes"]), 1)
        self.assertEqual(Run.objects.exclude(pk=self.run.pk).get().task, "render")
        Run.objects.filter(course=self.course).update(status="ready")
        with patch("app_portal.views.storage.write_json") as write:
            response = self.client.post(
                f"/courses/{self.course.id}/manage/",
                {"action": "rename", "title": "Ready <podcast> & learning"}, **self.headers,
            )
        run = Run.objects.get(status="queued")
        self.assertEqual(run.task, "render")
        self.assertEqual(response.url, f"/runs/{run.id}/")
        self.assertEqual(write.call_args.args[1]["title"], "Ready <podcast> & learning")
        response = self.client.get(f"/courses/{self.course.id}/manage/", **self.headers)
        self.assertContains(response, "Ready &lt;podcast&gt; &amp; learning")
        self.assertNotContains(response, "Ready <podcast>")
        Run.objects.filter(course=self.course).update(status="ready")
        self.course.status, self.course.manifest = "needs_review", self.manifest
        self.course.save()
        previous_runs = Run.objects.count()
        with (
            patch("app_portal.views.storage.delete_blobs"),
            patch("app_portal.views.storage.write_json"),
        ):
            response = self.client.post(
                f"/courses/{self.course.id}/manage/",
                {"action": "delete_episode", "episode": 1}, **self.headers,
            )
        self.assertEqual(response.url, f"/courses/{self.course.id}/manage/")
        self.assertEqual(Run.objects.count(), previous_runs)
        self.course.refresh_from_db()
        self.assertEqual(len(self.course.manifest["episodes"]), 1)

    def test_delete_last_episode_deletes_course_and_blobs(self):
        self.run.status = "ready"
        self.run.save()
        self.course.manifest["episodes"] = self.course.manifest["episodes"][:1]
        self.course.save()
        with patch("app_portal.views.storage.delete_prefix") as delete:
            response = self.client.post(
                f"/courses/{self.course.id}/manage/",
                {
                    "action": "delete_episode",
                    "episode": 1,
                },
                **self.headers,
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
