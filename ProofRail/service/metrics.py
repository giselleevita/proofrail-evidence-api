from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UsageEvent:
    api_key_id: str
    customer_id: str
    route: str
    status_code: int
    latency_ms: float
    ts: str
    request_id: str | None = None
