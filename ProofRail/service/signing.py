from __future__ import annotations

import hashlib
import hmac

# Algorithm identifiers carried in the signature envelope so a bundle is
# self-describing and a verifier never has to guess how it was signed.
ALG_HMAC_SHA256 = "hmac-sha256"
ALG_ED25519 = "ed25519"


def sign_bytes(secret: bytes, payload: bytes) -> str:
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def verify_bytes(secret: bytes, payload: bytes, signature_hex: str) -> bool:
    expected = sign_bytes(secret, payload)
    return hmac.compare_digest(expected, signature_hex)


# ---------------------------------------------------------------------------
# Ed25519 (asymmetric)
#
# HMAC is symmetric: anyone able to verify a bundle also holds the secret and can
# therefore forge one. That is adequate for tamper-evidence inside our own trust
# boundary, but it cannot support the "independently verifiable by a third party"
# property an auditor or banking partner needs. Ed25519 signatures verify with a
# *public* key only, so a recipient can check authenticity without gaining the
# ability to mint bundles.
# ---------------------------------------------------------------------------


def _ed25519_private_key(seed: bytes):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if len(seed) != 32:
        raise ValueError("Ed25519 private seed must be exactly 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(seed)


def ed25519_public_key_hex(seed: bytes) -> str:
    """Public key (hex) for a 32-byte Ed25519 private seed."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    public = _ed25519_private_key(seed).public_key()
    return public.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def sign_ed25519(seed: bytes, payload: bytes) -> str:
    """Sign payload with a 32-byte Ed25519 private seed; returns a hex signature."""
    return _ed25519_private_key(seed).sign(payload).hex()


def verify_ed25519(public_key_hex: str, payload: bytes, signature_hex: str) -> bool:
    """Verify an Ed25519 signature using only the public key. Never raises."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        public_bytes = bytes.fromhex(public_key_hex)
        signature = bytes.fromhex(signature_hex)
    except (ValueError, TypeError):
        return False
    if len(public_bytes) != 32:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, payload)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
