from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# Pulumi's Azure Blob backend reads this file; on a hosted runner its credential chain otherwise
# stops at the runner's unavailable managed identity, before the Azure CLI login.
url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
url += "&" + urlencode({"audience": "api://AzureADTokenExchange"})
request = Request(
    url, headers={"Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]},
)
with urlopen(request, timeout=30) as response:
    token = json.load(response)["value"]
path = Path(os.environ["RUNNER_TEMP"]) / "azure-federated-token.jwt"
descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(descriptor, "w") as file:
    file.write(token)
with open(os.environ["GITHUB_ENV"], "a") as file:
    file.write(f"AZURE_FEDERATED_TOKEN_FILE={path}\n")
