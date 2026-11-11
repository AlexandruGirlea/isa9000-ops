from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils import timezone
from isa9000.links import MAX_URL_CHARS


def link_checks_exceeded(user, count: int) -> bool:
    key = f"link-checks:{user.pk}:{timezone.now():%Y%m%d%H%M}"
    cache.add(key, 0, 120)
    return cache.incr(key, count) > 60


def source_url(value):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or "\\" in value
            or any(ord(c) < 33 or ord(c) == 127 for c in value)
            or "%" in host
        ):
            raise ValueError
        host = host.rstrip(".").lower()
        port = 443 if parsed.scheme == "https" else 80
        if parsed.port not in {None, port} or len(value) > MAX_URL_CHARS:
            raise ValueError
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if (
            host == "localhost"
            or host.endswith(".localhost")
            or (address is None and "." not in host)
        ):
            raise ValueError
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
        if not ips or any(
            not address.is_global
            or address.is_multicast
            or str(getattr(address, "ipv4_mapped", None) or address) == "168.63.129.16"
            for address in ips
        ):
            raise ValueError
    except (ValueError, OSError) as error:
        raise ValidationError("Use a public HTTP or HTTPS source link.") from error
    return value
