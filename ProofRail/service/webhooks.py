from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(ts: str) -> datetime:
    # Accept the "Z" suffix we emit everywhere.
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def sign_webhook(*, secret: str, body: bytes) -> str:
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    status_code: int | None
    error: str | None


def deliver_once(
    *,
    url: str,
    secret: str,
    event_type: str,
    event_id: str,
    payload_json: str,
    timeout_s: float,
) -> DeliveryResult:
    body = payload_json.encode("utf-8")
    headers = {
        "content-type": "application/json",
        "user-agent": "proofrail-webhooks/1.0",
        "x-proofrail-event-type": event_type,
        "x-proofrail-event-id": event_id,
        "x-proofrail-signature": sign_webhook(secret=secret, body=body),
    }
    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=False) as client:
            resp = client.post(url, content=body, headers=headers)
        ok = 200 <= int(resp.status_code) < 300
        return DeliveryResult(
            ok=ok, status_code=int(resp.status_code), error=None if ok else "http_error"
        )
    except Exception as exc:  # noqa: BLE001
        return DeliveryResult(ok=False, status_code=None, error=type(exc).__name__)


def next_attempt_ts(*, now: str, attempt_count: int, retry_base_s: float) -> str:
    # Exponential backoff with a simple base. attempt_count is the *current* count before increment.
    delay = retry_base_s * (2 ** max(0, min(int(attempt_count), 16)))
    dt = _parse_iso(now) + timedelta(seconds=float(delay))
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_event_payload(
    *, event_type: str, event_id: str, customer_id: str, data: dict[str, Any]
) -> str:
    payload = {
        "schema_version": "1",
        "event_type": event_type,
        "event_id": event_id,
        "customer_id": customer_id,
        "created_at": utc_now_iso(),
        "data": data,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
