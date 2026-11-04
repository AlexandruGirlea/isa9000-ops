from __future__ import annotations

import os
import time
from urllib.parse import urlsplit
from uuid import UUID

import requests

from shared import payloads, telemetry


class CallbackError(RuntimeError):
    pass


class Callbacks:
    def __init__(self, task: dict) -> None:
        base = os.environ["ISA9000_CALLBACK_BASE_URL"].rstrip("/")
        parts = urlsplit(base)
        local = bool(os.environ.get("ISA9000_SERVICEBUS_CONNECTION_STRING"))
        if (
            parts.scheme not in ({"http", "https"} if local else {"https"})
            or not parts.hostname or parts.username or parts.password
            or parts.path or parts.query or parts.fragment
        ):
            raise CallbackError("Invalid callback base URL.")
        self.run_id = str(UUID(task["run_id"]))
        self.base = f"{base}/internal/runs/{self.run_id}"
        self.headers = {
            "Authorization": f"Bearer {os.environ['ISA9000_CALLBACK_SECRET']}",
            "X-Correlation-Id": task["correlation_id"],
        }

    def post(self, action: str, body: dict) -> dict:
        envelope = payloads.encrypt(body, f"callback:{self.run_id}:{action}")
        for attempt in range(6):
            try:
                response = requests.post(
                    f"{self.base}/{action}",
                    json=envelope,
                    headers={**self.headers, **telemetry.inject()},
                    timeout=10,
                    allow_redirects=False,
                )
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 5:
                    raise CallbackError("Callback connection failed after retries.") from None
            else:
                if response.status_code == 200:
                    try:
                        return payloads.decrypt(response.json(), f"reply:{self.run_id}:{action}")
                    except ValueError:
                        raise CallbackError("Callback response could not be verified.") from None
                if response.status_code < 500 or attempt == 5:
                    raise CallbackError(f"Callback returned HTTP {response.status_code}.")
            time.sleep(2**attempt)
        raise CallbackError("Callback failed.")
