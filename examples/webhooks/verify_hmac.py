#!/usr/bin/env python3
"""Verify ProofRail webhook delivery signatures (x-proofrail-signature).

Matches ProofRail.service.webhooks.sign_webhook: HMAC-SHA256 over the raw body,
header value ``sha256=<hex>``.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path


def sign_webhook(*, secret: str, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def verify(*, secret: str, body: bytes, signature_header: str) -> bool:
    expected = sign_webhook(secret=secret, body=body)
    return hmac.compare_digest(expected.encode("ascii"), signature_header.strip().encode("ascii"))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--secret", default="", help="Subscription secret (or set PROOFRAIL_WEBHOOK_SECRET)"
    )
    p.add_argument(
        "body_path",
        nargs="?",
        default="",
        help="Path to raw JSON body file captured from a delivery",
    )
    p.add_argument(
        "signature",
        nargs="?",
        default="",
        help="Signature header value, e.g. sha256=...",
    )
    args = p.parse_args()

    secret = args.secret or os.environ.get("PROOFRAIL_WEBHOOK_SECRET", "")
    if not secret:
        print("Provide --secret or set PROOFRAIL_WEBHOOK_SECRET", file=sys.stderr)
        return 2

    if not args.body_path or not args.signature:
        demo_body = json.dumps(
            {
                "schema_version": "1",
                "event_type": "screening.created",
                "event_id": "ex",
                "data": {},
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        sig = sign_webhook(secret=secret, body=demo_body)
        ok = verify(secret=secret, body=demo_body, signature_header=sig)
        print("Demo payload verifies:", ok)
        return 0 if ok else 1

    body = Path(args.body_path).read_bytes()
    if verify(secret=secret, body=body, signature_header=args.signature):
        print("ok: signature matches")
        return 0
    print("reject: signature mismatch", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
