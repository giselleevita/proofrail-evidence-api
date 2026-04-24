from __future__ import annotations

import json
import signal
import time

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

    while not stop.is_set:
        process_once(state)
        time.sleep(0.5)
    return 0


def process_once(state) -> None:  # noqa: ANN001
    now = utc_now_iso()
    jobs = state.db.list_due_jobs(now=now, limit=100)
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


def main() -> None:
    raise SystemExit(run())
