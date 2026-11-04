from __future__ import annotations

import base64
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_ENVELOPE_BYTES = 4 * ((MAX_PAYLOAD_BYTES + 30) // 3) + 128


def _cipher():
    try:
        key = base64.b64decode(os.environ["ISA9000_PAYLOAD_KEY"], validate=True)
        if len(key) != 32:
            raise ValueError
    except (KeyError, ValueError):
        raise ValueError("Payload encryption is not configured.") from None
    return AESGCM(key)


def encrypt(payload: dict, purpose: str) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Invalid payload.")
    nonce = os.urandom(12)
    plaintext = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(plaintext) > MAX_PAYLOAD_BYTES:
        raise ValueError("Payload exceeds the size limit.")
    ciphertext = _cipher().encrypt(nonce, plaintext, f"isa9000:v1:{purpose}".encode("ascii"))
    return {"v": 1, "payload": base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")}


def decrypt(envelope: dict, purpose: str) -> dict:
    try:
        if (
            not isinstance(envelope, dict) or set(envelope) != {"v", "payload"}
            or type(envelope["v"]) is not int or envelope["v"] != 1
            or not isinstance(envelope["payload"], str)
            or len(envelope["payload"]) > MAX_ENVELOPE_BYTES - 128
        ):
            raise ValueError
        data = base64.b64decode(envelope["payload"], altchars=b"-_", validate=True)
        if not 28 <= len(data) <= MAX_PAYLOAD_BYTES + 28:
            raise ValueError
        plaintext = _cipher().decrypt(
            data[:12], data[12:], f"isa9000:v1:{purpose}".encode("ascii"),
        )
        payload = json.loads(plaintext)
        if not isinstance(payload, dict):
            raise ValueError
    except (InvalidTag, KeyError, TypeError, ValueError):
        raise ValueError("Encrypted payload could not be verified.") from None
    return payload
