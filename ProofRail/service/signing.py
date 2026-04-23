from __future__ import annotations

import hashlib
import hmac


def sign_bytes(secret: bytes, payload: bytes) -> str:
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_bytes(secret: bytes, payload: bytes, signature_hex: str) -> bool:
    expected = sign_bytes(secret, payload)
    return hmac.compare_digest(expected, signature_hex)

