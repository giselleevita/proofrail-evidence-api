#!/usr/bin/env python3
"""Generate an Ed25519 signing keypair for ProofRail evidence bundles.

The private seed goes in PROOFRAIL_ED25519_KEYS (keep it secret); the public key is
safe to publish so auditors can verify bundles offline.

Usage:
    python scripts/generate_signing_key.py --key-id k1
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ProofRail.service.signing import ed25519_public_key_hex  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-id", default="k1", help="Key id to label this key")
    args = parser.parse_args()

    seed = os.urandom(32)
    seed_hex = seed.hex()
    public_hex = ed25519_public_key_hex(seed)

    print(f"key_id:     {args.key_id}")
    print(f"public key: {public_hex}")
    print()
    print("# Private seed - store as a secret, never commit:")
    print(f'PROOFRAIL_ED25519_KEYS="{args.key_id}:{seed_hex}"')
    print(f'PROOFRAIL_ED25519_KEY_CURRENT="{args.key_id}"')


if __name__ == "__main__":
    main()
