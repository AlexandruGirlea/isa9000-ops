from __future__ import annotations

import copy
import json
import shlex
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from markupsafe import escape

from shared import payloads
from worker import __main__ as entrypoint
from worker import callbacks, runner


class Recorder:
    def __init__(self, cancel_stage=None):
        self.calls = []
        self.cancel_stage = cancel_stage

    def post(self, action, body):
        self.calls.append((action, copy.deepcopy(body)))
        if action == "progress":
            return {"cancel": body.get("stage") == self.cancel_stage}
        return {"accepted": True, "ok": True}

    @property
    def result(self):
        return next(body for action, body in reversed(self.calls) if action == "result")


@pytest.fixture
def task(monkeypatch):
    monkeypatch.setenv("ISA9000_CALLBACK_BASE_URL", "https://web.internal.example")
    return {
        "run_id": "1a799497-23cd-48b7-91bf-c572ad7d2ade",
        "course_id": "16a163b7-1fce-405c-b6dc-47c807937834",
        "correlation_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        "task": "create",
        "request": "A learning course; $(this is not a shell)",
        "sources": ["https://example.com/handbook"],
        "follow_links": False,
        "links": {
            "media": "/media/16a163b7-1fce-405c-b6dc-47c807937834/",
            "static": "/static/isa9000/", "home": "/libraries/sales/",
        },
        "callback": "http://localhost/internal/runs/1a799497-23cd-48b7-91bf-c572ad7d2ade",
    }


@pytest.fixture
def local_worker(monkeypatch, branding_config, stub_cli):
    monkeypatch.setenv("ISA9000_CONFIG", str(branding_config))
    monkeypatch.setenv("ISA9000_CLI", shlex.join(stub_cli))
    monkeypatch.setenv("ISA9000_CALLBACK_SECRET", "test-callback-secret")
    blobs, snapshots, requested = {}, [], []

    def upload(folder, prefix):
        contents = {
            path.relative_to(folder).as_posix(): path.read_bytes()
            for path in folder.rglob("*") if path.is_file()
        }
        blobs.update({f"{prefix}/{name}": data for name, data in contents.items()})
        snapshots.append(contents)

    def download(prefix, folder):
        for name, data in blobs.items():
            if name.startswith(prefix + "/"):
                path = folder / name.removeprefix(prefix + "/")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

    def sources(names, folder):
        requested.extend(names)
        paths = []
        for name in names:
            path = folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blobs[name])
            paths.append(path)
        return paths

    monkeypatch.setattr(runner.storage, "upload_folder", upload)
    monkeypatch.setattr(runner.storage, "download_prefix", download)
    monkeypatch.setattr(runner.storage, "download_blobs", sources)
    return blobs, snapshots, requested


@pytest.mark.parametrize("code,state,title", [
    (0, "ready", 'Sales <tools> & "teams"; $(not-a-shell)'),
    (0, "ready", None),
    (1, "failed", "Sales onboarding"),
    (3, "needs_review", "Sales onboarding"),
])
def test_stub_outcomes(code, state, title, monkeypatch, task, local_worker, caplog):
    monkeypatch.setenv("ISA9000_STUB_EXIT", str(code))
    if title is not None:
        task["title"] = title
    task["description"] = 'A creator summary with <text> & "quotes".'
    start = MagicMock(wraps=runner.subprocess.Popen)
    monkeypatch.setattr(runner.subprocess, "Popen", start)
    recorder = Recorder()
    assert runner.run_task(task, recorder) == (code == 1)
    result = recorder.result
    assert result["state"] == state
    assert task["request"] not in caplog.text
    command = start.call_args_list[0].args[0]
    prompt = command[command.index("--request") + 1]
    assert prompt == task["request"] + (
        "\n\nUse this exact podcast title: " + json.dumps(title) if title else ""
    )
    if code == 1:
        assert result["error_source"] == "isa9000"
        assert result["error"] == "Demo source could not be read.\nRetry with ISA9000_STUB_EXIT=0."
        assert result["manifest"] is None
    else:
        assert result["manifest"]["episodes"]
        assert local_worker[1]
        expected_title = title or "Welcome to the learning library"
        assert result["manifest"]["title"] == expected_title
        final = json.loads(local_worker[1][-1]["podcast.json"])
        assert final["title"] == expected_title
        assert final["description"] == task["description"]
    if code == 0:
        assert "<!DOCTYPE html>" in result["html"]
        assert task["links"]["media"] in result["html"]
        assert "/static/isa9000/vendor/bootstrap.min.css" in result["html"]
        early = json.loads(local_worker[1][0]["podcast.json"])
        assert early["title"] == "Welcome to the learning library"
        assert all(episode["audio"] is None for episode in early["episodes"])
        assert len(local_worker[1]) == 2
        assert str(escape(expected_title)) in result["html"]
        assert str(escape(task["description"])) in result["html"]
    else:
        assert result["html"] is None


def test_record_after_review_and_render(monkeypatch, task, local_worker):
    monkeypatch.setenv("ISA9000_STUB_EXIT", "3")
    recorder = Recorder()
    runner.run_task(task, recorder)
    monkeypatch.setenv("ISA9000_STUB_EXIT", "0")
    task["task"] = "record"
    task["title"] = "Reviewed course title"
    assert runner.run_task(task, recorder) == 0
    assert recorder.result["manifest"]["title"] == task["title"]
    assert all(episode["audio"] for episode in recorder.result["manifest"]["episodes"])
    task["manifest"] = copy.deepcopy(recorder.result["manifest"])
    edited = task["manifest"]["episodes"][0]
    old_audio = edited["audio"]
    edited.update(transcript="01 - revision-test.txt", audio=None, seconds=None, edited=True)
    task["manifest"]["review"]["edited"] = True
    prefix = f"courses/{task['course_id']}/"
    local_worker[0][prefix + edited["transcript"]] = b"The creator edited this transcript."
    unchanged = task["manifest"]["episodes"][1]["audio"]
    unchanged_bytes = local_worker[0][prefix + unchanged]
    recorder.calls.clear()
    assert runner.run_task(task, recorder) == 0
    assert recorder.result["manifest"]["episodes"][0]["audio"] == "01 - revision-test.mp3"
    assert "The creator edited this transcript." in recorder.result["html"]
    assert local_worker[0][prefix + unchanged] == unchanged_bytes
    assert prefix + old_audio in local_worker[0]
    assert {body.get("current") for action, body in recorder.calls
            if action == "progress" and body.get("stage") == "record"} == {1}
    task["manifest"] = copy.deepcopy(recorder.result["manifest"])
    audio_before = {name: data for name, data in local_worker[0].items() if name.endswith(".mp3")}
    task["task"] = "render"
    task["title"] = "New <display> title"
    task["description"] = "An updated description"
    monkeypatch.setattr(runner.subprocess, "Popen", MagicMock(side_effect=AssertionError))
    assert runner.run_task(task, recorder) == 0
    runner.subprocess.Popen.assert_not_called()
    assert recorder.result["manifest"]["title"] == task["title"]
    assert str(escape(task["title"])) in recorder.result["html"]
    assert task["description"] in recorder.result["html"]
    assert audio_before == {
        name: data for name, data in local_worker[0].items() if name.endswith(".mp3")
    }
    assert "This local demonstration uses silent audio" in recorder.result["html"]


def test_downloads_exact_original_upload_names(monkeypatch, task, local_worker):
    monkeypatch.setenv("ISA9000_STUB_EXIT", "3")
    name = "uploads/original-run/pricing.pdf"
    local_worker[0][name] = b"demo source"
    task["sources"].append(name)
    assert runner.run_task(task, Recorder()) == 0
    assert local_worker[2] == [name]


def test_cancel_keeps_no_recordings_and_reports_no_manifest(monkeypatch, task, local_worker):
    monkeypatch.setenv("ISA9000_STUB_EXIT", "0")
    recorder = Recorder(cancel_stage="record")
    assert runner.run_task(task, recorder) == 0
    assert recorder.result["state"] == "stopped"
    assert recorder.result["html"] is None
    assert recorder.result["manifest"] is None
    assert not any(name.endswith(".mp3") for upload in local_worker[1] for name in upload)


def test_heartbeat_stops_silent_process(monkeypatch, task, local_worker):
    monkeypatch.setattr(runner, "HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setenv(
        "ISA9000_CLI", shlex.join([sys.executable, "-c", "import time; time.sleep(60)"])
    )
    kill = MagicMock(wraps=runner.os.killpg)
    monkeypatch.setattr(runner.os, "killpg", kill)
    recorder = Recorder(cancel_stage="heartbeat")
    assert runner.run_task(task, recorder) == 0
    assert recorder.result["state"] == "stopped"
    assert kill.call_args.args[1] == signal.SIGTERM


def test_concurrent_foundry_token_refreshes(monkeypatch, task, tmp_path):
    monkeypatch.delenv("IDENTITY_ENDPOINT", raising=False)
    run = runner.Run(task, Recorder(), tmp_path)
    run.credential = MagicMock()
    run.credential.get_token.return_value.token = "test-foundry-token"
    write_text = Path.write_text

    def slow_write(path, *args, **kwargs):
        result = write_text(path, *args, **kwargs)
        # Keep the shared temporary file in place while the other refresh starts.
        time.sleep(0.02)
        return result

    monkeypatch.setattr(Path, "write_text", slow_write)
    start = threading.Barrier(2)

    def refresh():
        start.wait(timeout=5)
        run.refresh_token()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(refresh) for _ in range(2)]
        for future in futures:
            future.result(timeout=5)

    assert run.token_file.read_text(encoding="utf-8") == "test-foundry-token"
    assert run.credential.get_token.call_count == 2
    assert not run.token_file.with_suffix(".tmp").exists()


@pytest.mark.parametrize("invalid", [
    {"sources": ["/etc/passwd"]},
    {"sources": ["file:///tmp/secret"]},
    {"sources": ["ftp://example.com/a"]},
    {"title": None},
    {"title": 12},
    {"title": "x" * 501},
    {"description": ["invalid"]},
    {"description": "x" * 10001},
    {"task": "record", "manifest": {}},
    {"task": "record", "manifest": "invalid"},
])
def test_refuses_noncontract_sources(invalid, task, local_worker, monkeypatch):
    task.update(invalid)
    start = MagicMock(side_effect=AssertionError)
    monkeypatch.setattr(runner.subprocess, "Popen", start)
    recorder = Recorder()
    assert runner.run_task(task, recorder) == 1
    assert recorder.result["error_source"] == "worker"
    start.assert_not_called()


@pytest.mark.parametrize("operation", ["upload_folder", "render_course"])
def test_worker_failure_preserves_manifest(operation, monkeypatch, task, local_worker, caplog):
    monkeypatch.setenv("ISA9000_STUB_EXIT", "0")
    owner = runner.storage if operation == "upload_folder" else runner
    monkeypatch.setattr(owner, operation, MagicMock(side_effect=RuntimeError("secret content")))
    recorder = Recorder()
    assert runner.run_task(task, recorder) == 1
    assert recorder.result["error_source"] == "worker"
    assert recorder.result["manifest"]
    assert recorder.result["html"] is None
    assert "secret content" not in caplog.text


def test_callback_retries_and_headers(monkeypatch, task):
    monkeypatch.setenv("ISA9000_CALLBACK_SECRET", "test-secret")
    monkeypatch.setattr(callbacks.telemetry, "inject", lambda: {"traceparent": "test-parent"})
    reply = payloads.encrypt({"accepted": True}, f"reply:{task['run_id']}:accept")
    post = MagicMock(side_effect=[requests.ConnectionError(), MagicMock(status_code=503),
                                 MagicMock(status_code=200, json=lambda: reply)])
    sleep = MagicMock()
    monkeypatch.setattr(callbacks.requests, "post", post)
    monkeypatch.setattr(callbacks.time, "sleep", sleep)
    assert callbacks.Callbacks(task).post("accept", {}) == {"accepted": True}
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2]
    assert post.call_args.kwargs["headers"] == {
        "Authorization": "Bearer test-secret", "X-Correlation-Id": task["correlation_id"],
        "traceparent": "test-parent",
    }


@pytest.mark.parametrize("accepted", [True, False, None])
def test_one_shot_accept_complete_or_abandon(accepted, monkeypatch, task):
    monkeypatch.setenv("ISA9000_SERVICEBUS_CONNECTION_STRING", "local-emulator")
    monkeypatch.setattr(entrypoint.telemetry, "setup", lambda _: None)
    message = MagicMock(application_properties={}, message_id=task["run_id"])
    message.__str__.return_value = json.dumps(payloads.encrypt(task, f"task:{task['run_id']}"))
    receiver = MagicMock()
    receiver.__enter__.return_value = receiver
    receiver.receive_messages.return_value = [message]
    client = MagicMock()
    client.get_queue_receiver.return_value = receiver
    factory = MagicMock(return_value=client)
    monkeypatch.setattr(entrypoint.ServiceBusClient, "from_connection_string", factory)
    callback = MagicMock()
    if accepted is None:
        callback.post.side_effect = callbacks.CallbackError("unavailable")
    else:
        callback.post.return_value = {"accepted": accepted}
    monkeypatch.setattr(entrypoint, "Callbacks", lambda _: callback)
    renewer = MagicMock()
    monkeypatch.setattr(entrypoint, "AutoLockRenewer", renewer)
    run = MagicMock(side_effect=lambda *_: 9 if receiver.complete_message.called else 0)
    monkeypatch.setattr(entrypoint, "run_task", run)
    assert entrypoint.main() == (1 if accepted is None else 0)
    receiver.receive_messages.assert_called_once_with(max_message_count=1, max_wait_time=30)
    assert receiver.complete_message.call_count == (accepted is not None)
    assert receiver.abandon_message.call_count == (accepted is None)
    assert run.call_count == (accepted is True)


def test_idle_managed_identity(monkeypatch):
    monkeypatch.delenv("ISA9000_SERVICEBUS_CONNECTION_STRING", raising=False)
    monkeypatch.setenv("ISA9000_SERVICEBUS_NAMESPACE", "demo.servicebus.windows.net")
    monkeypatch.setattr(entrypoint.telemetry, "setup", lambda _: None)
    receiver = MagicMock()
    receiver.__enter__.return_value = receiver
    receiver.receive_messages.return_value = []
    client = MagicMock()
    client.get_queue_receiver.return_value = receiver
    factory = MagicMock(return_value=client)
    credential = MagicMock()
    monkeypatch.setattr(entrypoint, "ServiceBusClient", factory)
    monkeypatch.setattr(entrypoint, "DefaultAzureCredential", lambda: credential)
    assert entrypoint.main() == 0
    factory.assert_called_once_with("demo.servicebus.windows.net", credential=credential)
