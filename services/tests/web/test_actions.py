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
from isa9000.config import load_config
from opentelemetry import trace

from shared import payloads
from shared.generation import generation_defaults

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

    def test_regenerating_one_episode_queues_a_recording_of_that_episode_only(self):
        self.run.status = "ready"
        self.run.save()
        path = f"/courses/{self.course.id}/manage/"
        before = (self.course.status, self.course.html, self.course.manifest)
        with patch("app_portal.runs.dispatch") as send, self.captureOnCommitCallbacks(
            execute=True,
        ):
            response = self.client.post(
                path, {"action": "rerecord_episode", "episode": 2}, **self.headers,
            )
        run = Run.objects.get(status="queued")
        self.assertEqual(response.url, f"/runs/{run.id}/")
        self.assertEqual((run.task, run.episodes), ("record", [2]))
        self.assertEqual([step["key"] for step in run.steps], ["record", "done"])
        send.assert_called_once()
        self.assertTrue(LogEntry.objects.filter(
            user=self.user, change_message__contains="episode.rerecord_requested",
        ).exists())
        # Nothing about the published podcast changes until the new recording arrives.
        self.course.refresh_from_db()
        self.assertEqual((self.course.status, self.course.html, self.course.manifest), before)
        page = self.client.get(path, **self.headers)
        self.assertContains(page, "Recording new audio for episode 2")
        self.assertNotContains(page, 'value="rerecord_episode"')
        self.assertContains(self.client.get(f"/runs/{run.id}/", **self.headers), "Record episode 2")
        listening = self.client.get(f"/courses/{self.course.id}/", **self.headers)
        self.assertEqual(listening.content, self.course.html.encode())

    def test_regenerating_is_refused_when_there_is_nothing_to_replace(self):
        path = f"/courses/{self.course.id}/manage/"

        def regenerate(episode):
            return self.client.post(
                path, {"action": "rerecord_episode", "episode": episode}, **self.headers,
            ).status_code

        # A run is still in progress.
        self.assertEqual(regenerate(1), 409)
        self.run.status = "ready"
        self.run.save()
        self.assertEqual(regenerate(9), 404)
        self.assertEqual(regenerate(""), 404)
        self.course.manifest["episodes"][0].update(audio=None, seconds=None)
        self.course.save()
        self.assertEqual(regenerate(1), 409)
        # Awaiting approval: approving records what is missing.
        self.in_review()
        self.assertEqual(regenerate(2), 409)
        self.assertEqual(Run.objects.count(), 1)

    def test_a_failed_regeneration_leaves_the_podcast_as_it_was(self):
        self.run.status = "ready"
        self.run.save()
        run = Run.objects.create(
            course=self.course, requested_by=self.user, task="record", episodes=[1],
        )
        self.callback("accept", run=run)
        page = self.client.get(f"/runs/{run.id}/", **self.headers)
        self.assertContains(page, "Recording new audio")
        self.assertContains(page, "keeps playing its current audio")
        # The worker recorded a new take under a new name, then failed before it could publish.
        staged = {**self.manifest, "episodes": [
            {**self.manifest["episodes"][0], "audio": "01 - take-new.mp3", "seconds": 75},
            self.manifest["episodes"][1],
        ]}
        self.callback("result", {
            "state": "failed", "manifest": staged, "error": "The page could not be built",
            "error_source": "worker",
        }, run=run)
        self.course.refresh_from_db()
        self.assertEqual((self.course.status, self.course.html), ("ready", "<!doctype html>Course"))
        self.assertEqual(self.course.manifest, self.manifest)
        page = self.client.get(f"/runs/{run.id}/", **self.headers)
        self.assertContains(page, "The new audio was not recorded")
        self.assertContains(page, "still plays its previous audio")
        self.assertNotContains(page, 'value="retry"')
        # The button is back, so the creator can start again.
        manage = self.client.get(f"/courses/{self.course.id}/manage/", **self.headers)
        self.assertContains(manage, 'value="rerecord_episode"', count=2)
        self.assertContains(manage, '<div class="workspace-column">', count=2)

    def test_a_finished_regeneration_replaces_the_page_and_duration(self):
        self.run.status = "ready"
        self.run.save()
        run = Run.objects.create(
            course=self.course, requested_by=self.user, task="record", episodes=[1],
        )
        self.callback("accept", run=run)
        manifest = {**self.manifest, "episodes": [
            {**self.manifest["episodes"][0], "seconds": 75}, self.manifest["episodes"][1],
        ]}
        self.callback(
            "result", {"state": "ready", "manifest": manifest, "html": "New page"}, run=run,
        )
        self.course.refresh_from_db()
        self.assertEqual((self.course.status, self.course.html), ("ready", "New page"))
        self.assertEqual(self.course.manifest["episodes"][0]["seconds"], 75)

    def test_pronunciations_are_edited_on_manage_and_used_by_the_next_recording(self):
        self.run.status = "ready"
        self.run.save()
        self.course.generation_settings = generation_defaults(load_config())
        self.course.save()
        path = f"/courses/{self.course.id}/manage/"
        refused = self.client.post(
            path, {"action": "pronunciations", "pronunciations": "GLE G L E"}, **self.headers,
        )
        self.assertContains(refused, "Use one pronunciation per line")
        response = self.client.post(
            path, {"action": "pronunciations", "pronunciations": "GLE=G L E\nnginx = engine x",
                   "name": "Unrequested narrator", "quality": "thorough"},
            **self.headers,
        )
        self.assertEqual(response.url, path)
        spoken = {"GLE": "G L E", "nginx": "engine x"}
        self.course.refresh_from_db()
        self.assertEqual(
            self.course.generation_settings,
            {**generation_defaults(load_config()), "pronunciations": spoken},
        )
        self.assertTrue(LogEntry.objects.filter(
            user=self.user, change_message__contains="pronunciations",
        ).exists())
        self.assertContains(self.client.get(path, **self.headers), "GLE = G L E")
        with patch("app_portal.runs.dispatch"):
            self.client.post(path, {"action": "rerecord_episode", "episode": 1}, **self.headers)
        run = Run.objects.get(status="queued")
        self.assertEqual(run.generation_settings["pronunciations"], spoken)

    def test_manage_lists_the_links_and_file_names_the_podcast_was_made_from(self):
        path = f"/courses/{self.course.id}/manage/"
        self.run.sources = ["https://example.invalid/a?b=1&c=2", "uploads/x/1-price-list.pdf"]
        self.run.follow_links = True
        self.run.save()
        page = self.client.get(path, **self.headers)
        self.assertContains(page, 'href="https://example.invalid/a?b=1&amp;c=2"')
        self.assertContains(page, "price-list.pdf")
        self.assertNotContains(page, "uploads/x")
        self.assertContains(page, "were also read, up to 25.")
        self.run.follow_links = False
        self.run.save()
        self.assertContains(self.client.get(path, **self.headers), "were not read.")

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
        # Without chosen episodes the task says nothing, and isa9000 records what is missing.
        self.assertNotIn("episodes", body)
        self.assertEqual(body["description"], self.course.description)
        self.assertEqual(body["links"]["home"], f"/libraries/{SALES}/")
        self.assertEqual(body["callback"], f"http://testserver/internal/runs/{self.run.id}")
        self.assertEqual(message.message_id, str(self.run.id))
        self.assertEqual(message.application_properties, {"traceparent": "parent"})
        self.run.episodes = [2]
        self.run.save()
        with (
            patch.dict("os.environ", {"ISA9000_SERVICEBUS_CONNECTION_STRING": "local-test"}),
            patch("app_portal.runs.ServiceBusClient", factory),
        ):
            dispatch(self.run)
        message = sender.__enter__.return_value.send_messages.call_args.args[0]
        body = payloads.decrypt(json.loads(b"".join(message.body)), f"task:{self.run.id}")
        self.assertEqual(body["episodes"], [2])

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
