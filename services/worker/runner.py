from __future__ import annotations

import contextlib
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import urlsplit

from azure.core.exceptions import ResourceNotFoundError
from isa9000.config import load_config, update_config
from isa9000.podcast import load_manifest

from shared import storage, telemetry
from shared.generation import validate_generation
from worker.callbacks import CallbackError, Callbacks
from worker.render import render_course

HEARTBEAT_SECONDS = 60
STOP_TIMEOUT = 10
# Ends a stuck run before the job's 7200-second replica timeout, leaving time to report it.
TASK_SECONDS = 6600
logger = telemetry.log(__name__)


class WorkerError(RuntimeError):
    pass


class Run:
    def __init__(self, task: dict, callbacks: Callbacks, root: Path) -> None:
        self.task = task
        self.callbacks = callbacks
        self.root = root
        self.kb = root / "kb"
        self.folder: Path | None = None
        self.manifest: dict | None = None
        self.title = ""
        self.description = None
        self.process: subprocess.Popen | None = None
        self.cancelled = threading.Event()
        self.finished = threading.Event()
        self.heartbeat_error: Exception | None = None
        self.timed_out = False
        self.error = ""
        self.stderr = ""
        self.saved_early = False
        self.upload_attempted = False
        self.fields = {
            key: task[key] for key in ("run_id", "course_id", "task", "correlation_id")
        }
        self.fields.update(
            group_id=task.get("group_id", ""), requested_by=task.get("requested_by", ""),
        )

    def stop_process(self) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()

    def progress(self, event: dict) -> None:
        reply = self.callbacks.post("progress", event)
        counts = {
            key: event[key] for key in ("current", "total")
            if isinstance(event.get(key), (int, float))
        }
        logger.info(
            "CLI progress.", extra={**self.fields, "stage": event.get("stage", ""), **counts}
        )
        if reply.get("cancel"):
            self.cancelled.set()
            self.stop_process()

    def heartbeat(self, properties: dict) -> None:
        deadline = time.monotonic() + TASK_SECONDS
        with telemetry.attach(properties):
            while not self.finished.wait(HEARTBEAT_SECONDS):
                if time.monotonic() > deadline:
                    self.timed_out = True
                    self.stop_process()
                    return
                try:
                    self.progress({"stage": "heartbeat"})
                except Exception as error:
                    self.heartbeat_error = error
                    self.stop_process()
                    return

    def prepare(self) -> list[str]:
        self.kb.mkdir()
        if self.task["task"] in {"record", "render"}:
            self.folder = self.kb / "course"
            storage.download_prefix(f"courses/{self.task['course_id']}", self.folder)
            if not (self.folder / "podcast.json").is_file():
                raise WorkerError("The saved course could not be found.")
            if "manifest" in self.task:
                (self.folder / "podcast.json").write_text(
                    json.dumps(self.task["manifest"], ensure_ascii=False), encoding="utf-8"
                )
            self.refresh_manifest()
            return []
        if self.task["task"] != "create":
            raise WorkerError("Unknown worker task.")
        sources = self.task["sources"]
        uploads = []
        for source in sources:
            if source.startswith("uploads/"):
                uploads.append(source)
            else:
                parts = urlsplit(source)
                if parts.scheme not in {"http", "https"} or not parts.netloc:
                    raise WorkerError("Sources must be HTTP links or uploaded files.")
        try:
            downloaded = storage.download_blobs(uploads, self.root / "sources")
        except ResourceNotFoundError:
            raise WorkerError(
                "Uploaded sources are no longer available; uploads expire after 7 days."
            ) from None
        local = dict(zip(uploads, downloaded, strict=True))
        return [str(local.get(source, source)) for source in sources]

    def refresh_manifest(self, apply_title: bool = True) -> None:
        self.manifest = load_manifest(self.folder)
        if apply_title and (self.process is None or self.process.poll() is not None):
            metadata = {}
            if self.title:
                metadata["title"] = self.title
            if self.description is not None and (self.description or self.task["task"] != "create"):
                metadata["description"] = self.description
            if any(self.manifest.get(key) != value for key, value in metadata.items()):
                self.manifest.update(metadata)
                (self.folder / "podcast.json").write_text(
                    json.dumps(self.manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )

    def save(self, apply_title: bool = True) -> None:
        if self.folder is not None and (self.folder / "podcast.json").is_file():
            self.refresh_manifest(apply_title)
            self.upload_attempted = True
            storage.upload_folder(self.folder, f"courses/{self.task['course_id']}")

    def read_stderr(self) -> None:
        for line in self.process.stderr:
            if line.strip():
                self.stderr = line.strip().removeprefix("isa9000: ")

    def cli(self, sources: list[str], config_path: str, record: bool = False) -> int:
        command = shlex.split(os.environ.get("ISA9000_CLI", "")) or [
            sys.executable, "-m", "worker.cli"
        ]
        command += ["--progress", "json", "--config", config_path]
        if self.task["task"] == "create" and not record:
            request = self.task["request"]
            if self.title:
                request += "\n\nUse this exact podcast title: " + json.dumps(self.title)
            command += [
                "create", "--audio-kb", str(self.kb), "--request", request
            ]
            for source in sources:
                command += ["--source", source]
            if self.task.get("follow_links"):
                command.append("--follow-links")
            if self.task.get("text_only"):
                command.append("--text-only")
        else:
            command += ["record", str(self.folder)]
        environment = {
            key: os.environ[key] for key in (
                "PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP",
                "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
                "AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_CONFIG_DIR",
                "IDENTITY_ENDPOINT", "IDENTITY_HEADER",
                "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
                "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION", "GOOGLE_GENAI_USE_VERTEXAI",
                "ISA9000_STUB_EXIT", "ISA9000_STUB_STEP_DELAY",
            ) if key in os.environ
        }
        for key in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY"):
            if f"ISA9000_{key}" in os.environ:
                environment[key] = os.environ[f"ISA9000_{key}"]
        self.process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
            env=environment,
        )
        errors = threading.Thread(target=self.read_stderr, daemon=True)
        errors.start()
        try:
            if self.cancelled.is_set() or self.heartbeat_error:
                self.stop_process()
            for line in self.process.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("folder"):
                    folder = Path(event["folder"]).resolve()
                    if folder.parent != self.kb.resolve():
                        raise WorkerError("CLI output folder is outside its workspace.")
                    self.folder = folder
                if event.get("stage") == "error":
                    self.error = event.get("message", "")
                if event.get("stage") == "record" and not self.saved_early:
                    self.save(apply_title=False)
                    self.saved_early = True
                    self.upload_attempted = False
                self.progress(event)
            return self.process.wait()
        finally:
            self.stop_process()
            errors.join(timeout=STOP_TIMEOUT)
            self.process.stdout.close()
            self.process.stderr.close()

    def execute(self) -> dict:
        result = {"state": "failed", "manifest": None, "html": None,
                  "error": "", "error_source": ""}
        heartbeat = threading.Thread(
            target=self.heartbeat, args=(telemetry.inject(),), daemon=True
        )
        heartbeat.start()
        try:
            title = self.task.get("title", "")
            if not isinstance(title, str) or len(title) > 500:
                raise WorkerError("Podcast title must be text of at most 500 characters.")
            self.title = title.strip()
            description = self.task.get("description")
            if description is not None and (
                not isinstance(description, str) or len(description) > 10000
            ):
                raise WorkerError("Invalid podcast description.")
            self.description = description
            if self.task["task"] in {"record", "render"} and "manifest" in self.task and (
                not isinstance(self.task["manifest"], dict)
                or not isinstance(self.task["manifest"].get("episodes"), list)
                or not self.task["manifest"]["episodes"]
            ):
                raise WorkerError("Invalid course manifest.")
            config_path = os.environ.get("ISA9000_CONFIG", "/config/isa9000.toml")
            config = load_config(config_path)
            if not isinstance(self.task.get("text_only", False), bool):
                raise WorkerError("Invalid transcript review setting.")
            overrides = self.task.get("generation_settings", {})
            if overrides:
                try:
                    values = validate_generation(overrides, config)
                except ValueError:
                    raise WorkerError("Invalid podcast settings.") from None
                temporary_config = self.root / "isa9000.toml"
                temporary_config.touch(mode=0o600)
                temporary_config.write_text(Path(config_path).read_text(encoding="utf-8"))
                settings = {
                    "narrator": {key: values[key] for key in ("name", "voice")},
                    "branding": {
                        key: values[key] for key in ("greeting", "ending", "final_ending")
                    },
                    "output": {"quality": values["quality"]},
                }
                if config.branding.logo:
                    settings["branding"]["logo"] = str(config.logo_file.resolve())
                if config.google.credentials_file:
                    settings["google"] = {
                        "credentials_file": str(config.resolve(config.google.credentials_file))
                    }
                update_config(temporary_config, settings, values["pronunciations"])
                config_path = str(temporary_config)
                config = load_config(config_path)
            elif not isinstance(overrides, dict):
                raise WorkerError("Invalid podcast settings.")
            sources = self.prepare()
            code = 0
            if not self.cancelled.is_set() and not self.heartbeat_error:
                if self.task["task"] != "render":
                    code = self.cli(sources, config_path)
                # Without a requested review, record past isa9000's own quality gate.
                if (code == 3 and self.task["task"] == "create" and not self.task.get("text_only")
                        and self.folder and not self.cancelled.is_set()):
                    code = self.cli(sources, config_path, record=True)
            if self.heartbeat_error:
                raise self.heartbeat_error
            if self.cancelled.is_set():
                result["state"] = "stopped"
            elif code == 0:
                result["state"] = (
                    "needs_review"
                    if self.task["task"] == "create" and self.task.get("text_only")
                    else "ready"
                )
            elif code == 3:
                reason = self.stderr.partition("review before recording: ")[2]
                result.update(state="needs_review", error=reason.partition(". Read the")[0])
            else:
                result.update(error=self.error or self.stderr or f"isa9000 exited with {code}.",
                              error_source="isa9000")
            # A canceled run keeps nothing: this workspace disappears with the task.
            if result["state"] != "stopped":
                self.save()
            if result["state"] == "ready":
                if self.manifest is None:
                    raise WorkerError("The CLI did not produce a course manifest.")
                result["html"] = render_course(
                    self.folder, self.root / "site", self.manifest, self.task["links"], config
                )
        except Exception as error:
            self.stop_process()
            logger.error("Worker task failed.\n%s", "".join(traceback.format_list(
                traceback.extract_tb(error.__traceback__)
            )), extra=self.fields)
            result.update(state="failed", html=None, error_source="worker",
                          error=str(error) if isinstance(error, (WorkerError, CallbackError))
                          else "The worker could not finish the task.")
            if not self.upload_attempted and not self.cancelled.is_set():
                try:
                    self.save()
                except Exception:
                    logger.error("Could not save the partial course.", extra=self.fields)
        finally:
            self.finished.set()
            heartbeat.join()
        if self.heartbeat_error and result["error_source"] != "worker":
            result.update(state="failed", html=None, error_source="worker",
                          error="The progress callback failed.")
        if self.timed_out and result["error_source"] != "worker":
            result.update(state="failed", html=None, error_source="worker",
                          error="The task took longer than the time limit.")
        if self.cancelled.is_set() and result["error_source"] != "worker":
            result.update(state="stopped", html=None, error="", error_source="")
        result["manifest"] = None if self.cancelled.is_set() else self.manifest
        return result


def run_task(task: dict, callbacks: Callbacks) -> int:
    with tempfile.TemporaryDirectory(prefix="isa9000-worker-") as root:
        result = Run(task, callbacks, Path(root)).execute()
        telemetry.audit(
            "worker.finished", actor_id="worker", state=result["state"],
            run_id=task["run_id"], course_id=task["course_id"], task=task["task"],
            group_id=task.get("group_id", ""), requested_by=task.get("requested_by", ""),
            operation_correlation_id=task["correlation_id"],
        )
        try:
            callbacks.post("result", result)
            telemetry.audit(
                "worker.result_delivered", actor_id="worker", run_id=task["run_id"],
                course_id=task["course_id"], operation_correlation_id=task["correlation_id"],
            )
        except Exception:
            logger.error("Result callback failed.", extra={
                key: task[key] for key in ("run_id", "course_id", "task", "correlation_id")
            })
            return 1
        return int(result["state"] == "failed")
