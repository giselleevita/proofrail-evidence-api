from __future__ import annotations

import json
import signal
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ProofRail.service.config import load_config
from ProofRail.service.state import build_state
from ProofRail.service.utils import utc_now_iso
from ProofRail.service.webhooks import deliver_once, next_attempt_ts


class _Stop:
    def __init__(self) -> None:
        self._stop = False

    def request(self, *_args) -> None:  # noqa: ANN001
        self._stop = True

    @property
    def is_set(self) -> bool:
        return self._stop


def run() -> int:
    cfg = load_config()
    state = build_state(cfg)

    stop = _Stop()
    signal.signal(signal.SIGINT, stop.request)
    signal.signal(signal.SIGTERM, stop.request)

    last_gc_at = 0.0
    while not stop.is_set:
        now_s = time.time()
        do_gc = (now_s - last_gc_at) >= 60.0
        process_once(state, do_gc=do_gc)
        if do_gc:
            last_gc_at = now_s
        time.sleep(0.5)
    return 0


def _gc_raw_fetches(*, raw_dir: Path, cutoff: datetime, customer_id: str | None) -> int:
    root = raw_dir / (customer_id or "")
    if not root.exists():
        return 0
    deleted = 0
    for p in root.rglob("*"):
        try:
            if not p.is_file():
                continue
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=UTC)
            if mtime >= cutoff:
                continue
            p.unlink()
            deleted += 1
        except Exception:  # noqa: BLE001
            continue
    return deleted


def _run_retention_gc(state) -> None:  # noqa: ANN001
    now_dt = datetime.now(UTC)
    usage_cutoff = now_dt - timedelta(days=int(state.cfg.usage_retention_days))
    evidence_cutoff = now_dt - timedelta(days=int(state.cfg.evidence_retention_days))

    usage_cutoff_iso = usage_cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    deleted_usage = 0
    try:
        deleted_usage = int(state.db.delete_usage_events_before(usage_cutoff_iso))
    except Exception:  # noqa: BLE001
        deleted_usage = 0

    deleted_packs = 0
    deleted_blobs = 0
    deleted_raw = 0
    try:
        deleted_packs = int(state.store.delete_packs_before(cutoff=evidence_cutoff, dry_run=False))
        deleted_blobs = int(
            state.store.delete_unreferenced_blobs(cutoff=evidence_cutoff, dry_run=False)
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        deleted_raw = int(
            _gc_raw_fetches(raw_dir=state.cfg.raw_dir, cutoff=evidence_cutoff, customer_id=None)
        )
    except Exception:  # noqa: BLE001
        pass

    try:
        state.db.insert_audit_event(
            ts=utc_now_iso(),
            actor="worker",
            action="gc_run",
            request_id=None,
            details_json=json.dumps(
                {
                    "usage_retention_days": int(state.cfg.usage_retention_days),
                    "evidence_retention_days": int(state.cfg.evidence_retention_days),
                    "deleted": {
                        "usage_events": deleted_usage,
                        "evidence_packs": deleted_packs,
                        "raw_fetch_files": deleted_raw,
                        "unreferenced_blobs": deleted_blobs,
                    },
                }
            ),
        )
    except Exception:  # noqa: BLE001
        pass


def process_once(state, *, do_gc: bool = False) -> None:  # noqa: ANN001
    now = utc_now_iso()
    lease_until = (
        (datetime.now(UTC) + timedelta(seconds=30))
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    jobs = state.db.claim_due_jobs(now=now, locked_until=lease_until, limit=100)
    for j in jobs:
        if str(j["job_type"]) != "webhook_delivery":
            state.db.mark_job_failed(job_id=int(j["job_id"]), now=now, error="unknown_job_type")
            continue
        try:
            payload = json.loads(str(j["payload_json"]))
            delivery_id = int(payload["delivery_id"])
        except Exception:  # noqa: BLE001
            state.db.mark_job_failed(job_id=int(j["job_id"]), now=now, error="bad_payload")
            continue

        d = state.db.get_webhook_delivery(delivery_id=delivery_id)
        if d is None or str(d.get("status")) not in {"queued", "retry"}:
            state.db.mark_job_success(job_id=int(j["job_id"]), now=now)
            continue
        if str(d.get("next_attempt_at")) > now:
            state.db.mark_job_retry(
                job_id=int(j["job_id"]),
                now=now,
                run_at=str(d.get("next_attempt_at") or now),
                error=None,
            )
            continue

        sub = state.db.get_webhook_subscription(subscription_id=str(d["subscription_id"]))
        if sub is None or not sub.get("active"):
            state.db.mark_webhook_delivery_failed(
                delivery_id=int(d["delivery_id"]), now=now, error="subscription_missing"
            )
            state.db.mark_job_success(job_id=int(j["job_id"]), now=now)
            continue

        res = deliver_once(
            url=str(sub["url"]),
            secret=str(sub["secret"]),
            event_type=str(d["event_type"]),
            event_id=str(d["event_id"]),
            payload_json=str(d["payload_json"]),
            timeout_s=float(state.cfg.webhook_timeout_s),
        )
        if res.ok:
            state.db.mark_webhook_delivery_success(
                delivery_id=int(d["delivery_id"]),
                now=now,
                status_code=int(res.status_code or 0),
            )
            state.db.mark_job_success(job_id=int(j["job_id"]), now=now)
        else:
            attempts = int(d["attempt_count"]) + 1
            if attempts >= int(state.cfg.webhook_max_attempts):
                state.db.mark_webhook_delivery_failed(
                    delivery_id=int(d["delivery_id"]), now=now, error=res.error
                )
                state.db.mark_job_success(job_id=int(j["job_id"]), now=now)
            else:
                state.db.mark_webhook_delivery_retry(
                    delivery_id=int(d["delivery_id"]),
                    now=now,
                    next_attempt_at=next_attempt_ts(
                        now=now,
                        attempt_count=int(d["attempt_count"]),
                        retry_base_s=float(state.cfg.webhook_retry_base_s),
                    ),
                    status_code=res.status_code,
                    error=res.error,
                )
                state.db.mark_job_success(job_id=int(j["job_id"]), now=now)

    if do_gc:
        _run_retention_gc(state)


def main() -> None:
    raise SystemExit(run())
