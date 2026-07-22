"""Ed25519 bundle signing: the property that makes evidence third-party verifiable.

HMAC bundles are only tamper-evident inside the issuer's trust boundary — a party able
to verify one also holds the secret and can forge one. These tests pin the asymmetric
property: a recipient holding ONLY the public key can verify authenticity, and cannot
produce a bundle that verifies.
"""

from __future__ import annotations

import os

import pytest

from ProofRail.service.signing import (
    ed25519_public_key_hex,
    sign_bytes,
    sign_ed25519,
    verify_bytes,
    verify_ed25519,
)


@pytest.fixture
def seed() -> bytes:
    return os.urandom(32)


class TestEd25519RoundTrip:
    def test_sign_and_verify_with_public_key_only(self, seed):
        payload = b'{"case_id":"c1","decision":"block"}'
        sig = sign_ed25519(seed, payload)
        # The verifier needs only the public key — no secret material.
        assert verify_ed25519(ed25519_public_key_hex(seed), payload, sig) is True

    def test_tampered_payload_fails(self, seed):
        sig = sign_ed25519(seed, b'{"decision":"block"}')
        tampered = b'{"decision":"allow"}'
        assert verify_ed25519(ed25519_public_key_hex(seed), tampered, sig) is False

    def test_wrong_public_key_fails(self, seed):
        payload = b"evidence"
        sig = sign_ed25519(seed, payload)
        other_public = ed25519_public_key_hex(os.urandom(32))
        assert verify_ed25519(other_public, payload, sig) is False

    def test_public_key_cannot_forge(self, seed):
        """The core non-repudiation property: the public key alone cannot sign."""
        public_hex = ed25519_public_key_hex(seed)
        payload = b"forged evidence"
        # Attempting to use the public key as a private seed must not yield a
        # signature that verifies against the real public key.
        forged = sign_ed25519(bytes.fromhex(public_hex), payload)
        assert verify_ed25519(public_hex, payload, forged) is False

    def test_public_key_is_stable_and_not_the_seed(self, seed):
        public_hex = ed25519_public_key_hex(seed)
        assert public_hex == ed25519_public_key_hex(seed)
        assert public_hex != seed.hex()
        assert len(bytes.fromhex(public_hex)) == 32


class TestMalformedInputs:
    def test_bad_hex_returns_false_not_raise(self, seed):
        sig = sign_ed25519(seed, b"x")
        assert verify_ed25519("nothex", b"x", sig) is False
        assert verify_ed25519(ed25519_public_key_hex(seed), b"x", "nothex") is False

    def test_wrong_length_public_key_returns_false(self):
        assert verify_ed25519("aabb", b"x", "cc" * 64) is False

    def test_invalid_seed_length_rejected(self):
        with pytest.raises(ValueError):
            sign_ed25519(b"tooshort", b"x")


class TestHmacStillWorks:
    """Back-compat: existing HMAC bundles must keep verifying."""

    def test_hmac_round_trip(self):
        secret = b"shared-secret"
        payload = b"legacy bundle"
        assert verify_bytes(secret, payload, sign_bytes(secret, payload)) is True

    def test_hmac_rejects_tamper(self):
        secret = b"shared-secret"
        sig = sign_bytes(secret, b"legacy bundle")
        assert verify_bytes(secret, b"tampered", sig) is False
