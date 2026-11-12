from __future__ import annotations

import signal
import subprocess
import sys
import threading

EXECUTION_TIMEOUT = 7200
RETRY_SECONDS = 10


def main() -> int:
    stopping = threading.Event()

    def drain(signum, frame):
        stopping.set()

    signal.signal(signal.SIGTERM, drain)
    signal.signal(signal.SIGINT, drain)
    while not stopping.is_set():
        try:
            result = subprocess.run(
                [sys.executable, "-m", "worker"],
                check=False,
                timeout=EXECUTION_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            # Exit the replica so any remaining task subprocesses are stopped with it.
            print("Worker execution timed out; restarting replica.", file=sys.stderr, flush=True)
            return 1
        if result.returncode:
            print(
                f"Worker execution exited with code {result.returncode}.",
                file=sys.stderr, flush=True,
            )
            stopping.wait(RETRY_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
