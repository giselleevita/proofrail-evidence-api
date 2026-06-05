from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

_BLOCKED_HOSTS = {"localhost"}
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan")


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


def _normalize_host(host: str) -> str:
    try:
        return host.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError as exc:
        raise ValueError("invalid_host") from exc


def _is_public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(ip.is_global)


def _assert_public_resolved_addresses(host: str, port: int) -> None:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError("dns_resolution_failed") from exc

    resolved: set[str] = set()
    for info in infos:
        address = info[4][0]
        resolved.add(address)
        if not _is_public_ip(address):
            raise ValueError("resolved_address_not_public")
    if not resolved:
        raise ValueError("dns_resolution_failed")


def _format_netloc(host: str, port: int, *, include_port: bool) -> str:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        host_part = host
    else:
        host_part = f"[{host}]" if ip.version == 6 else host
    return f"{host_part}:{port}" if include_port else host_part


def validate_webhook_url(url: str, *, resolve_dns: bool = False) -> str:
    """
    Validate outbound webhook targets against common SSRF paths.

    Creation-time validation blocks obvious unsafe literals. Delivery-time
    validation also resolves DNS so stored or rebinding-prone hostnames cannot
    target loopback, private, link-local, multicast, or reserved addresses.
    """
    raw = (url or "").strip()
    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise ValueError("invalid_url") from exc

    if parts.scheme.lower() != "https":
        raise ValueError("https_required")
    if parts.username or parts.password:
        raise ValueError("userinfo_not_allowed")
    if not parts.hostname:
        raise ValueError("host_required")
    if parts.fragment:
        raise ValueError("fragment_not_allowed")

    host = _normalize_host(parts.hostname)
    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_HOST_SUFFIXES):
        raise ValueError("host_not_allowed")
    if _is_public_ip(host) is False:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("ip_address_not_public")

    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("invalid_port") from exc
    if port is None:
        port = 443
    if port < 1 or port > 65535:
        raise ValueError("invalid_port")

    if resolve_dns:
        _assert_public_resolved_addresses(host, port)

    netloc = _format_netloc(host, port, include_port=parts.port is not None)
    return urlunsplit(("https", netloc, parts.path or "/", parts.query, ""))


def deliver_once(
    *,
    url: str,
    secret: str,
    event_type: str,
    event_id: str,
    payload_json: str,
    timeout_s: float,
) -> DeliveryResult:
    try:
        target_url = validate_webhook_url(url, resolve_dns=True)
    except ValueError:
        return DeliveryResult(ok=False, status_code=None, error="webhook_url_not_allowed")

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
            resp = client.post(target_url, content=body, headers=headers)
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
