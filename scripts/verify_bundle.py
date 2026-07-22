#!/usr/bin/env python3
"""Offline verifier for ProofRail evidence bundles.

This is the tool an auditor or banking partner runs. It contacts no server and needs
no shared secret: for Ed25519 bundles the public key carried in the bundle (or supplied
via --public-key) is sufficient to prove the bundle was signed by the holder of the
corresponding private key and has not been altered.

It also re-derives the case event hash-chain so a mutated or reordered timeline is
detected even if the outer signature were somehow accepted.

Usage:
    python scripts/verify_bundle.py --bundle bundle.json
    python scripts/verify_bundle.py --bundle bundle.json --public-key <hex>

Exit code 0 = verified, 1 = failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ProofRail.service.signing import (  # noqa: E402
    ALG_ED25519,
    ALG_HMAC_SHA256,
    verify_bytes,
    verify_ed25519,
)
from ProofRail.service.storage import canonical_json_bytes  # noqa: E402


def _sha256_hex(obj: object) -> str:
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


GENESIS_HASH = "0" * 64


def verify_event_chain(bundle: dict) -> tuple[bool, str]:
    """Recompute the append-only event hash-chain exactly as the service builds it.

    The service hashes a fixed four-key payload per event
    ({ts, actor, event_type, note}) chained as sha256(canonical({prev_hash, event})),
    starting from a genesis of 64 zeros. Reconstructing that payload from the emitted
    event fields reproduces the hash for the normal case where those values are
    strings (or a null note), which is what the API stores.
    """
    events = bundle.get("events") or []
    prev = GENESIS_HASH
    for idx, ev in enumerate(events, start=1):
        payload = {
            "ts": ev.get("ts"),
            "actor": ev.get("actor"),
            "event_type": ev.get("event_type"),
            "note": ev.get("note"),
        }
        if ev.get("prev_hash") != prev:
            return False, f"prev_hash mismatch at event {idx}"
        expected = _sha256_hex({"prev_hash": prev, "event": payload})
        if ev.get("hash") != expected:
            return False, f"hash mismatch at event {idx}"
        prev = expected

    head = bundle.get("chain_head")
    if head is not None and head != prev:
        return False, "chain_head does not match final event hash"
    return True, f"event chain intact ({len(events)} events)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, help="Path to bundle JSON")
    parser.add_argument("--public-key", help="Ed25519 public key hex (overrides bundle)")
    parser.add_argument("--hmac-secret", help="HMAC secret (legacy bundles only)")
    args = parser.parse_args()

    doc = json.loads(Path(args.bundle).read_text(encoding="utf-8"))

    # Accept either the full API response {bundle, signature} or a bare bundle.
    bundle = doc.get("bundle", doc)
    sig = doc.get("signature") or {}
    if not isinstance(sig, dict) or "signature" not in sig:
        print("FAIL: bundle has no signature envelope", file=sys.stderr)
        raise SystemExit(1)

    algorithm = sig.get("algorithm", ALG_HMAC_SHA256)
    payload = canonical_json_bytes(bundle)

    if algorithm == ALG_ED25519:
        public_key = args.public_key or sig.get("public_key")
        if not public_key:
            print("FAIL: no public key in bundle or --public-key", file=sys.stderr)
            raise SystemExit(1)
        ok = verify_ed25519(public_key, payload, sig["signature"])
        detail = f"ed25519, key_id={sig.get('key_id')}"
    elif algorithm == ALG_HMAC_SHA256:
        if not args.hmac_secret:
            print(
                "FAIL: this is a legacy hmac-sha256 bundle. It is NOT independently\n"
                "      verifiable — verifying requires the issuer's shared secret\n"
                "      (--hmac-secret), which would also let you forge bundles.\n"
                "      Ask the issuer to re-issue it as an ed25519 bundle.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        ok = verify_bytes(args.hmac_secret.encode("utf-8"), payload, sig["signature"])
        detail = f"hmac-sha256 (not third-party verifiable), key_id={sig.get('key_id')}"
    else:
        print(f"FAIL: unknown algorithm {algorithm!r}", file=sys.stderr)
        raise SystemExit(1)

    chain_ok, chain_msg = verify_event_chain(bundle)

    print(f"signature:   {'OK' if ok else 'FAIL'}  ({detail})")
    print(f"event chain: {'OK' if chain_ok else 'FAIL'}  ({chain_msg})")

    if ok and chain_ok:
        print("\nVERIFIED")
        raise SystemExit(0)
    print("\nVERIFICATION FAILED", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
