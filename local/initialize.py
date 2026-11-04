from __future__ import annotations

import os
import time
from urllib.error import URLError
from urllib.request import urlopen

from azure.core.exceptions import AzureError, ResourceExistsError
from azure.storage.blob import BlobServiceClient

client = BlobServiceClient.from_connection_string(
    os.environ["ISA9000_STORAGE_CONNECTION_STRING"],
    retry_total=0,
    connection_timeout=5,
    read_timeout=5,
)
for attempt in range(120):
    try:
        with urlopen("http://servicebus:5300/health", timeout=5) as response:
            if response.status != 200:
                raise OSError("Service Bus is not ready.")
        for name in ("courses", "uploads"):
            try:
                client.create_container(name)
            except ResourceExistsError:
                pass
        print("Local queue and blob containers are ready.")
        break
    except (AzureError, OSError, URLError):
        if attempt == 119:
            raise SystemExit("Local emulators did not become ready.") from None
        time.sleep(2)
client.close()
