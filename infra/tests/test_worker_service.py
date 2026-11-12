from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
from pathlib import Path

import pytest

SERVICE = Path(__file__).resolve().parents[1] / "worker_service.py"


def environment(tmp_path):
    return {**os.environ, "PYTHONPATH": os.pathsep.join((str(tmp_path), str(SERVICE.parent)))}


@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGINT])
def test_inline_service_repeats_idle_worker_then_drains_current_task(tmp_path, stop_signal):
    (tmp_path / "worker.py").write_text(
        "import os, signal, time\n"
        "from pathlib import Path\n"
        "counter = Path('calls')\n"
        "count = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "counter.write_text(str(count))\n"
        "if count == 2:\n"
        f"    os.kill(os.getppid(), {int(stop_signal)})\n"
        "    time.sleep(0.15)\n"
        "    Path('completed').touch()\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", SERVICE.read_text()],
        cwd=tmp_path, env=environment(tmp_path), capture_output=True, text=True, timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "calls").read_text() == "2"
    assert (tmp_path / "completed").exists()


def test_failed_worker_is_retried_without_stopping_service(tmp_path):
    (tmp_path / "worker.py").write_text(
        "import os, signal, sys\n"
        "from pathlib import Path\n"
        "counter = Path('calls')\n"
        "count = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "counter.write_text(str(count))\n"
        "if count == 1:\n"
        "    sys.exit(3)\n"
        "os.kill(os.getppid(), signal.SIGTERM)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", "import worker_service as s; s.RETRY_SECONDS = 0.01; "
         "raise SystemExit(s.main())"],
        cwd=tmp_path, env=environment(tmp_path), capture_output=True, text=True, timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "exited with code 3" in result.stderr
    assert (tmp_path / "calls").read_text() == "2"


def test_stop_interrupts_failure_backoff(tmp_path):
    (tmp_path / "worker.py").write_text("raise SystemExit(2)\n")
    with subprocess.Popen(
        [sys.executable, "-c", SERVICE.read_text()],
        cwd=tmp_path, env=environment(tmp_path), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    ) as process:
        try:
            readable, _, _ = select.select([process.stderr], [], [], 5)
            assert readable, "Worker did not report its failure"
            assert "exited with code 2" in process.stderr.readline()
            process.terminate()
            process.communicate(timeout=2)
            assert process.returncode == 0
        finally:
            if process.poll() is None:
                process.kill()


def test_hung_execution_ends_replica_and_reaps_worker(tmp_path):
    (tmp_path / "worker.py").write_text(
        "import os, time\n"
        "from pathlib import Path\n"
        "Path('pid').write_text(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", "import worker_service as s; s.EXECUTION_TIMEOUT = 0.5; "
         "raise SystemExit(s.main())"],
        cwd=tmp_path, env=environment(tmp_path), capture_output=True, text=True, timeout=5,
        check=False,
    )
    assert result.returncode == 1
    assert "timed out; restarting replica" in result.stderr
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "pid").read_text()), 0)
