from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass


def generate_api_key(prefix: str = "pr") -> str:
    raw = secrets.token_urlsafe(32)
    return f"{prefix}_{raw}"


def hash_api_key(api_key: str) -> str:
    # Store only the hash at rest.
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApiPrincipal:
    customer_id: str
    api_key_id: str
    scopes: frozenset[str]


def parse_scopes(scopes: str) -> frozenset[str]:
    parts = [s.strip() for s in scopes.split(",")]
    return frozenset([p for p in parts if p])
