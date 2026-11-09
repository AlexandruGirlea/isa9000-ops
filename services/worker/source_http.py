from __future__ import annotations

import ipaddress
import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
import urllib3

MAX_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_REQUESTS = 100
MAX_SECONDS = 900
PAGE_SECONDS = 90
READ_SECONDS = 75
MAX_REDIRECTS = 5


class SourceError(ValueError):
    pass


class SourceHTTP:
    RequestException = requests.RequestException

    def __init__(self):
        self.requests = 0
        self.bytes = 0
        self.seconds = 0.0
        self.lock = threading.Lock()

    def get(self, url, **options):
        if set(options) - {"headers", "timeout"}:
            raise requests.RequestException("Unsupported source request options.")
        headers = options.get("headers", {})
        timeout = options.get("timeout", 20)
        if (
            not isinstance(headers, dict)
            or set(headers) - {"User-Agent"}
            or not isinstance(headers.get("User-Agent", ""), str)
            or len(headers.get("User-Agent", "")) > 512
            or isinstance(timeout, bool)
            or not isinstance(timeout, (float, int))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise requests.RequestException("Unsupported source request options.")
        with self.lock:
            limit = min(MAX_BYTES, MAX_TOTAL_BYTES - self.bytes)
            seconds = min(PAGE_SECONDS, MAX_SECONDS - self.seconds)
            if self.requests >= MAX_REQUESTS or limit <= 0 or seconds <= 0:
                raise requests.RequestException("The source reading limit was reached.")
            self.requests += 1
            self.bytes += limit
            started = time.monotonic()
            try:
                # A process deadline also bounds DNS, slow headers and trickling chunked bodies.
                completed = subprocess.run(
                    [sys.executable, "-m", "worker.source_http"],
                    input=json.dumps({"url": url, "headers": headers, "limit": limit}).encode(),
                    capture_output=True,
                    timeout=seconds,
                    check=False,
                    env={
                        key: os.environ[key]
                        for key in ("PATH", "PYTHONPATH", "LANG", "LC_ALL")
                        if key in os.environ
                    },
                )
            except subprocess.TimeoutExpired:
                raise requests.RequestException("This source took too long to respond.") from None
            finally:
                self.seconds += time.monotonic() - started
            try:
                metadata, body = completed.stdout.split(b"\n", 1)
                result = json.loads(metadata)
                if len(body) > limit or not isinstance(result, dict):
                    raise ValueError
                if completed.returncode or "error" in result:
                    raise requests.RequestException(
                        result.get("error", "This source could not be read.")
                    )
                response = requests.Response()
                response.status_code = result["status"]
                response.url = result["url"]
                response.headers = requests.structures.CaseInsensitiveDict(result["headers"])
                response.encoding = requests.utils.get_encoding_from_headers(response.headers)
                response._content = body
                response._content_consumed = True
                self.bytes -= limit - len(body)
                return response
            except (KeyError, TypeError, ValueError):
                raise requests.RequestException("This source could not be read.") from None


def fetch(url, headers, limit):
    for redirect in range(MAX_REDIRECTS + 1):
        if not isinstance(url, str) or len(url) > 4096:
            raise SourceError("Use a public HTTP or HTTPS source link.")
        parsed = urlsplit(url)
        host = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or "\\" in url
            or any(ord(char) < 33 or ord(char) == 127 for char in url)
            or "%" in host
        ):
            raise SourceError("Use a public HTTP or HTTPS source link.")
        host = host.rstrip(".").encode("idna").decode("ascii").lower()
        port = 443 if parsed.scheme == "https" else 80
        if parsed.port not in {None, port}:
            raise SourceError("Source links must use the standard HTTP or HTTPS port.")
        if (
            host == "localhost"
            or host.endswith(".localhost")
            or ("." not in host and ":" not in host)
        ):
            raise SourceError("Use a public HTTP or HTTPS source link.")
        addresses = list(
            dict.fromkeys(
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            )
        )
        if not addresses or any(
            not address.is_global
            or address.is_multicast
            or str(getattr(address, "ipv4_mapped", None) or address) == "168.63.129.16"
            for address in addresses
        ):
            raise SourceError("Use a public HTTP or HTTPS source link.")
        host_header = f"[{host}]" if ":" in host else host
        path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        next_url = None
        for address in addresses:
            pool_type = urllib3.HTTPSConnectionPool if port == 443 else urllib3.HTTPConnectionPool
            tls = (
                {
                    "cert_reqs": "CERT_REQUIRED",
                    "ca_certs": requests.certs.where(),
                    "server_hostname": host,
                    "assert_hostname": host,
                }
                if port == 443
                else {}
            )
            with pool_type(str(address), port=port, **tls) as pool:
                try:
                    response = pool.urlopen(
                        "GET",
                        path,
                        headers={**headers, "Host": host_header, "Accept-Encoding": "identity"},
                        timeout=urllib3.Timeout(connect=5, read=READ_SECONDS),
                        retries=False,
                        redirect=False,
                        preload_content=False,
                    )
                except (
                    urllib3.exceptions.NewConnectionError,
                    urllib3.exceptions.ConnectTimeoutError,
                ):
                    if address == addresses[-1]:
                        raise
                    continue
                try:
                    if response.status in {301, 302, 303, 307, 308} and response.headers.get(
                        "Location"
                    ):
                        if redirect == MAX_REDIRECTS:
                            raise SourceError("This source redirects too many times.")
                        next_url = urljoin(url, response.headers["Location"])
                        break
                    if response.headers.get("Content-Encoding", "").strip().lower() not in {
                        "", "identity",
                    }:
                        raise SourceError("This source requires unsupported content compression.")
                    content_length = response.headers.get("Content-Length")
                    if content_length is not None and not 0 <= int(content_length) <= limit:
                        raise SourceError("The source reading size limit was reached.")
                    content_type = response.headers.get("Content-Type", "")
                    if len(content_type) > 2048:
                        raise SourceError("This source returned invalid response headers.")
                    content = bytearray()
                    while chunk := response.read(64 * 1024, decode_content=False):
                        content.extend(chunk)
                        if len(content) > limit:
                            raise SourceError("The source reading size limit was reached.")
                    return {
                        "status": response.status,
                        "url": url,
                        "headers": {"Content-Type": content_type},
                    }, content
                finally:
                    response.close()
        if next_url is None:
            raise SourceError("This source could not be reached.")
        url = next_url
    raise SourceError("This source redirects too many times.")


if __name__ == "__main__":
    try:
        payload = json.loads(sys.stdin.buffer.read(16385))
        if (
            len(sys.argv) != 1
            or not isinstance(payload, dict)
            or set(payload) != {"url", "headers", "limit"}
            or not isinstance(payload["limit"], int)
            or not 0 < payload["limit"] <= MAX_BYTES
        ):
            raise ValueError
        metadata, content = fetch(**payload)
    except (ValueError, OSError, urllib3.exceptions.HTTPError) as error:
        message = str(error) if isinstance(error, SourceError) else "This source could not be read."
        sys.stdout.write(json.dumps({"error": message}) + "\n")
        sys.exit(1)
    sys.stdout.buffer.write(json.dumps(metadata).encode() + b"\n" + content)
