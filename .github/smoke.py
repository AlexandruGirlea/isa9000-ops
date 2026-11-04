from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


url, group, app, ingress, image_tag = sys.argv[1:]
principal = base64.b64encode(json.dumps({
    "auth_typ": "aad",
    "claims": [
        {"typ": "oid", "val": "11111111-1111-4111-8111-111111111111"},
        {"typ": "tid", "val": os.environ["ARM_TENANT_ID"]},
        {"typ": "groups", "val": os.environ["ADMIN_GROUP_ID"]},
    ],
}).encode()).decode()
protected = Request(url.rstrip("/") + "/libraries/", headers={
    "X-MS-CLIENT-PRINCIPAL": principal,
})
for attempt in range(60):
    try:
        if ingress == "internal":
            revision = subprocess.check_output([
                "az", "containerapp", "show", "--resource-group", group, "--name", app,
                "--query", "properties.latestRevisionName", "--output", "tsv",
            ], text=True).strip()
            details = json.loads(subprocess.check_output([
                "az", "containerapp", "revision", "show", "--resource-group", group,
                "--name", app, "--revision", revision, "--output", "json",
            ], text=True))["properties"]
            image = details["template"]["containers"][0]["image"]
            separator = "@" if image_tag.startswith("sha256:") else ":"
            if details["healthState"] != "Healthy" or not image.endswith(separator + image_tag):
                raise OSError("The expected revision is not healthy.")
        else:
            with urlopen(url.rstrip("/") + "/healthz", timeout=10) as response:
                if response.status != 200:
                    raise OSError("The health endpoint is not ready.")
        try:
            with build_opener(NoRedirect()).open(protected, timeout=10):
                raise OSError("A forged identity header was accepted.")
        except HTTPError as error:
            if error.code not in {301, 302, 303, 307, 308, 401, 403}:
                raise OSError("The identity rejection check failed.") from error
        print("Deployment health and identity rejection checks passed.")
        break
    except (OSError, URLError, KeyError, ValueError, subprocess.CalledProcessError):
        if attempt == 59:
            raise SystemExit("Deployment health check failed.") from None
        time.sleep(5)
