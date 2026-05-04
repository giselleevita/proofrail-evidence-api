"""CORS middleware installer for ProofRail.

Reads PROOFRAIL_CORS_ORIGINS from environment:
  - Comma-separated list of allowed origins, e.g.:
      https://dashboard.yourdomain.com,https://proofrail.up.railway.app
  - Set to * only for local dev / demo mode (not recommended in prod).
  - If unset and PROOFRAIL_DEMO_MODE=1, defaults to ["*"] (demo permissive).
  - If unset and PROOFRAIL_DEMO_MODE=0, defaults to [] (no cross-origin requests).
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def install_cors(app: FastAPI) -> None:
    raw = os.environ.get("PROOFRAIL_CORS_ORIGINS", "").strip()
    demo_mode = os.environ.get("PROOFRAIL_DEMO_MODE", "0") == "1"

    if raw:
        origins = [o.strip() for o in raw.split(",") if o.strip()]
    elif demo_mode:
        # Demo mode: allow all origins so the web dashboard works out of the box.
        origins = ["*"]
    else:
        # Production with no explicit config: deny all cross-origin requests.
        origins = []

    if not origins:
        return

    allow_all = "*" in origins

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins if not allow_all else ["*"],
        # Credentials (Authorization header) cannot be sent with allow_origins=["*"].
        # In prod, origins are explicit so credentials are safe to allow.
        allow_credentials=not allow_all,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Correlation-ID",
            "X-Api-Key",
        ],
        expose_headers=[
            "X-Correlation-ID",
            "X-Next-Cursor",
            "x-next-cursor",
        ],
        max_age=600,  # preflight cache: 10 minutes
    )
