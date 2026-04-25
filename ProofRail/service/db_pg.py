from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class DbPgConfig:
    url: str


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS customers (
  customer_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
  api_key_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL REFERENCES customers(customer_id),
  key_sha256 TEXT NOT NULL UNIQUE,
  scopes TEXT NOT NULL,
  created_at TEXT NOT NULL,
  revoked_at TEXT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_keys_customer_id ON api_keys(customer_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_sha ON api_keys(key_sha256);

CREATE TABLE IF NOT EXISTS usage_events (
  id BIGSERIAL PRIMARY KEY,
  ts TEXT NOT NULL,
  api_key_id TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  route TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  latency_ms DOUBLE PRECISION NOT NULL,
  request_id TEXT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_events_ts ON usage_events(ts);
CREATE INDEX IF NOT EXISTS idx_usage_events_key ON usage_events(api_key_id);
CREATE INDEX IF NOT EXISTS idx_usage_events_customer_ts ON usage_events(customer_id, ts);

CREATE TABLE IF NOT EXISTS plans (
  customer_id TEXT PRIMARY KEY REFERENCES customers(customer_id),
  plan_id TEXT NOT NULL,
  rpm INTEGER NOT NULL,
  evidence_retention_days INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
  id BIGSERIAL PRIMARY KEY,
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  request_id TEXT NULL,
  details_json TEXT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_events_ts ON audit_events(ts);

CREATE TABLE IF NOT EXISTS screenings (
  screening_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  evidence_pack_id TEXT NOT NULL,
  list_version TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_screenings_customer_created ON screenings(customer_id, created_at);

CREATE TABLE IF NOT EXISTS screening_review_decisions (
  screening_id TEXT PRIMARY KEY REFERENCES screenings(screening_id),
  decided_at TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  evidence_pack_id TEXT NOT NULL,
  outcome TEXT NOT NULL,
  note TEXT NULL
);

CREATE TABLE IF NOT EXISTS cases (
  case_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  screening_id TEXT NOT NULL REFERENCES screenings(screening_id),
  evidence_pack_id TEXT NOT NULL,
  status TEXT NOT NULL,
  assignee TEXT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_customer_updated ON cases(customer_id, updated_at);

CREATE TABLE IF NOT EXISTS case_events (
  id BIGSERIAL PRIMARY KEY,
  case_id TEXT NOT NULL REFERENCES cases(case_id),
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,
  event_type TEXT NOT NULL,
  note TEXT NULL
);
CREATE INDEX IF NOT EXISTS idx_case_events_case_ts ON case_events(case_id, ts);

CREATE TABLE IF NOT EXISTS webhook_subscriptions (
  subscription_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL,
  url TEXT NOT NULL,
  secret TEXT NOT NULL,
  events TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webhook_subscriptions_customer ON webhook_subscriptions(customer_id);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
  delivery_id BIGSERIAL PRIMARY KEY,
  subscription_id TEXT NOT NULL REFERENCES webhook_subscriptions(subscription_id),
  customer_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  event_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  next_attempt_at TEXT NOT NULL,
  last_attempt_at TEXT NULL,
  last_status_code INTEGER NULL,
  last_error TEXT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_due ON webhook_deliveries(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_customer ON webhook_deliveries(customer_id, created_at);

CREATE TABLE IF NOT EXISTS jobs (
  job_id BIGSERIAL PRIMARY KEY,
  job_type TEXT NOT NULL,
  job_key TEXT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,
  locked_until TEXT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  run_at TEXT NOT NULL,
  last_error TEXT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(status, run_at);
CREATE INDEX IF NOT EXISTS idx_jobs_locked_until ON jobs(locked_until);
CREATE INDEX IF NOT EXISTS idx_jobs_key ON jobs(job_type, job_key);

CREATE TABLE IF NOT EXISTS idempotency_keys (
  customer_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  idem_key TEXT NOT NULL,
  request_sha256 TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  response_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(customer_id, scope, idem_key)
);
CREATE INDEX IF NOT EXISTS idx_idempotency_customer_scope ON idempotency_keys(customer_id, scope, created_at);
"""


class ProofRailDbPg:
    """
    Minimal Postgres backend mirroring `ProofRailDb` methods used by the API.

    Uses psycopg (imported lazily) so local dev/tests don't require Postgres deps.
    """

    def __init__(self, cfg: DbPgConfig) -> None:
        self.cfg = cfg
        self._init()

    def _connect(self):  # noqa: ANN001
        import psycopg

        return psycopg.connect(self.cfg.url, autocommit=True)

    def _init(self) -> None:
        # Guard against concurrent init (API + worker can start together).
        # Even with IF NOT EXISTS, Postgres can error under races for the
        # implicit composite types created alongside tables.
        with self._connect() as con:
            con.execute("SELECT pg_advisory_lock(741_224_001)")
            try:
                stmts = [s.strip() for s in SCHEMA_SQL.split(";") if s.strip()]
                for s in stmts:
                    con.execute(s)
                # Back-compat migration for existing pilot DBs.
                con.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS job_key TEXT NULL")
                con.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS locked_until TEXT NULL")
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_jobs_locked_until ON jobs(locked_until)"
                )
                con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_key ON jobs(job_type, job_key)")
                # Idempotency keys table (best-effort).
                con.execute(
                    "CREATE TABLE IF NOT EXISTS idempotency_keys (customer_id TEXT NOT NULL, scope TEXT NOT NULL, idem_key TEXT NOT NULL, request_sha256 TEXT NOT NULL, status_code INTEGER NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(customer_id, scope, idem_key))"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_idempotency_customer_scope ON idempotency_keys(customer_id, scope, created_at)"
                )
            finally:
                con.execute("SELECT pg_advisory_unlock(741_224_001)")

    # --- compatibility surface (subset) ---

    def ensure_customer(self, customer_id: str, created_at: str) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO customers(customer_id, created_at) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (customer_id, created_at),
            )

    def create_api_key(
        self, api_key_id: str, customer_id: str, key_sha256: str, scopes: str, created_at: str
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO api_keys(api_key_id, customer_id, key_sha256, scopes, created_at) VALUES (%s,%s,%s,%s,%s)",
                (api_key_id, customer_id, key_sha256, scopes, created_at),
            )

    def resolve_api_key(self, key_sha256: str) -> tuple[str, str, str] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT api_key_id, customer_id, scopes FROM api_keys WHERE key_sha256=%s AND revoked_at IS NULL",
                (key_sha256,),
            ).fetchone()
            if row is None:
                return None
            return (str(row[0]), str(row[1]), str(row[2]))

    def revoke_api_key(self, api_key_id: str, revoked_at: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE api_keys SET revoked_at=%s WHERE api_key_id=%s",
                (revoked_at, api_key_id),
            )

    def insert_audit_event(
        self,
        *,
        ts: str,
        actor: str,
        action: str,
        request_id: str | None,
        details_json: str | None,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO audit_events(ts, actor, action, request_id, details_json) VALUES (%s,%s,%s,%s,%s)",
                (ts, actor, action, request_id, details_json),
            )

    def insert_usage_events(self, payloads: list[dict[str, Any]]) -> None:
        if not payloads:
            return
        with self._connect() as con:
            for p in payloads:
                con.execute(
                    "INSERT INTO usage_events(ts, api_key_id, customer_id, route, status_code, latency_ms, request_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        p["ts"],
                        p["api_key_id"],
                        p["customer_id"],
                        p["route"],
                        int(p["status_code"]),
                        float(p["latency_ms"]),
                        p.get("request_id"),
                    ),
                )

    def usage_summary(self, customer_id: str, since_ts: str) -> dict[str, int]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT route, COUNT(*) FROM usage_events WHERE customer_id=%s AND ts >= %s GROUP BY route",
                (customer_id, since_ts),
            ).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    def delete_usage_events_before(self, cutoff_ts: str) -> int:
        with self._connect() as con:
            cur = con.execute("DELETE FROM usage_events WHERE ts < %s", (cutoff_ts,))
            return int(cur.rowcount or 0)

    def get_plan(self, customer_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            r = con.execute(
                "SELECT customer_id, plan_id, rpm, evidence_retention_days, created_at FROM plans WHERE customer_id=%s",
                (customer_id,),
            ).fetchone()
            if r is None:
                return None
        return {
            "customer_id": str(r[0]),
            "plan_id": str(r[1]),
            "rpm": int(r[2]),
            "evidence_retention_days": int(r[3]),
            "created_at": str(r[4]),
        }

    def insert_screening(
        self,
        *,
        screening_id: str,
        created_at: str,
        customer_id: str,
        evidence_pack_id: str,
        list_version: str,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO screenings(screening_id, created_at, customer_id, evidence_pack_id, list_version) "
                "VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT(screening_id) DO UPDATE SET evidence_pack_id=EXCLUDED.evidence_pack_id, list_version=EXCLUDED.list_version",
                (screening_id, created_at, customer_id, evidence_pack_id, list_version),
            )

    def get_screening(self, screening_id: str) -> dict[str, str] | None:
        with self._connect() as con:
            r = con.execute(
                "SELECT screening_id, created_at, customer_id, evidence_pack_id, list_version FROM screenings WHERE screening_id=%s",
                (screening_id,),
            ).fetchone()
            if r is None:
                return None
        return {
            "screening_id": str(r[0]),
            "created_at": str(r[1]),
            "customer_id": str(r[2]),
            "evidence_pack_id": str(r[3]),
            "list_version": str(r[4]),
        }

    def upsert_screening_review_decision(
        self,
        *,
        screening_id: str,
        decided_at: str,
        customer_id: str,
        evidence_pack_id: str,
        outcome: str,
        note: str | None,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO screening_review_decisions(screening_id, decided_at, customer_id, evidence_pack_id, outcome, note) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(screening_id) DO UPDATE SET decided_at=EXCLUDED.decided_at, customer_id=EXCLUDED.customer_id, evidence_pack_id=EXCLUDED.evidence_pack_id, outcome=EXCLUDED.outcome, note=EXCLUDED.note",
                (screening_id, decided_at, customer_id, evidence_pack_id, outcome, note),
            )

    def get_screening_review_decision_by_evidence_pack(
        self, *, evidence_pack_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as con:
            r = con.execute(
                "SELECT screening_id, decided_at, customer_id, evidence_pack_id, outcome, note "
                "FROM screening_review_decisions WHERE evidence_pack_id=%s",
                (evidence_pack_id,),
            ).fetchone()
            if r is None:
                return None
        return {
            "screening_id": str(r[0]),
            "decided_at": str(r[1]),
            "customer_id": str(r[2]),
            "evidence_pack_id": str(r[3]),
            "outcome": str(r[4]),
            "note": str(r[5]) if r[5] is not None else None,
        }

    def upsert_case(
        self,
        *,
        case_id: str,
        created_at: str,
        updated_at: str,
        customer_id: str,
        screening_id: str,
        evidence_pack_id: str,
        status: str,
        assignee: str | None = None,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO cases(case_id, created_at, updated_at, customer_id, screening_id, evidence_pack_id, status, assignee) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(case_id) DO UPDATE SET updated_at=EXCLUDED.updated_at, status=EXCLUDED.status, evidence_pack_id=EXCLUDED.evidence_pack_id, assignee=COALESCE(EXCLUDED.assignee, cases.assignee)",
                (
                    case_id,
                    created_at,
                    updated_at,
                    customer_id,
                    screening_id,
                    evidence_pack_id,
                    status,
                    assignee,
                ),
            )

    def get_case(self, *, case_id: str) -> dict[str, str] | None:
        with self._connect() as con:
            r = con.execute(
                "SELECT case_id, created_at, updated_at, customer_id, screening_id, evidence_pack_id, status, assignee FROM cases WHERE case_id=%s",
                (case_id,),
            ).fetchone()
            if r is None:
                return None
        return {
            "case_id": str(r[0]),
            "created_at": str(r[1]),
            "updated_at": str(r[2]),
            "customer_id": str(r[3]),
            "screening_id": str(r[4]),
            "evidence_pack_id": str(r[5]),
            "status": str(r[6]),
            "assignee": str(r[7]) if r[7] is not None else "",
        }

    def list_cases(
        self,
        *,
        customer_id: str,
        status: str | None,
        assignee: str | None = None,
        limit: int,
    ) -> list[dict[str, str]]:
        limit_n = max(1, min(200, int(limit)))
        q = (
            "SELECT case_id, created_at, updated_at, customer_id, screening_id, evidence_pack_id, status, assignee "
            "FROM cases WHERE customer_id=%s"
        )
        args: list[Any] = [customer_id]
        if status is not None:
            q += " AND status=%s"
            args.append(status)
        if assignee is not None:
            q += " AND assignee=%s"
            args.append(assignee)
        q += " ORDER BY updated_at DESC LIMIT %s"
        args.append(limit_n)
        with self._connect() as con:
            rows = con.execute(q, tuple(args)).fetchall()
        return [
            {
                "case_id": str(r[0]),
                "created_at": str(r[1]),
                "updated_at": str(r[2]),
                "customer_id": str(r[3]),
                "screening_id": str(r[4]),
                "evidence_pack_id": str(r[5]),
                "status": str(r[6]),
                "assignee": str(r[7]) if r[7] is not None else "",
            }
            for r in rows
        ]

    def insert_case_event(
        self,
        *,
        case_id: str,
        ts: str,
        actor: str,
        event_type: str,
        note: str | None,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO case_events(case_id, ts, actor, event_type, note) VALUES (%s,%s,%s,%s,%s)",
                (case_id, ts, actor, event_type, note),
            )

    def list_case_events(self, *, case_id: str, limit: int = 200) -> list[dict[str, str]]:
        limit_n = max(1, min(500, int(limit)))
        with self._connect() as con:
            rows = con.execute(
                "SELECT ts, actor, event_type, note FROM case_events WHERE case_id=%s ORDER BY ts ASC LIMIT %s",
                (case_id, limit_n),
            ).fetchall()
        return [
            {
                "ts": str(r[0]),
                "actor": str(r[1]),
                "event_type": str(r[2]),
                "note": str(r[3]) if r[3] is not None else "",
            }
            for r in rows
        ]

    # --- webhooks ---

    def create_webhook_subscription(
        self,
        *,
        subscription_id: str,
        customer_id: str,
        url: str,
        secret: str,
        events: list[str],
        created_at: str,
        active: bool = True,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO webhook_subscriptions(subscription_id, customer_id, url, secret, events, active, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    subscription_id,
                    customer_id,
                    url,
                    secret,
                    json.dumps(sorted(events)),
                    1 if active else 0,
                    created_at,
                ),
            )

    def list_webhook_subscriptions(self, *, customer_id: str) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT subscription_id, customer_id, url, secret, events, active, created_at "
                "FROM webhook_subscriptions WHERE customer_id=%s ORDER BY created_at DESC",
                (customer_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "subscription_id": str(r[0]),
                    "customer_id": str(r[1]),
                    "url": str(r[2]),
                    "secret": str(r[3]),
                    "events": json.loads(str(r[4])) if r[4] else [],
                    "active": bool(int(r[5])),
                    "created_at": str(r[6]),
                }
            )
        return out

    def get_webhook_subscription(self, *, subscription_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            r = con.execute(
                "SELECT subscription_id, customer_id, url, secret, events, active, created_at "
                "FROM webhook_subscriptions WHERE subscription_id=%s",
                (subscription_id,),
            ).fetchone()
            if r is None:
                return None
        return {
            "subscription_id": str(r[0]),
            "customer_id": str(r[1]),
            "url": str(r[2]),
            "secret": str(r[3]),
            "events": json.loads(str(r[4])) if r[4] else [],
            "active": bool(int(r[5])),
            "created_at": str(r[6]),
        }

    def delete_webhook_subscription(self, *, subscription_id: str, customer_id: str) -> bool:
        with self._connect() as con:
            con.execute(
                "DELETE FROM webhook_deliveries WHERE subscription_id=%s AND customer_id=%s",
                (subscription_id, customer_id),
            )
            cur = con.execute(
                "DELETE FROM webhook_subscriptions WHERE subscription_id=%s AND customer_id=%s",
                (subscription_id, customer_id),
            )
            return int(cur.rowcount or 0) > 0

    def enqueue_webhook_delivery(
        self,
        *,
        subscription_id: str,
        customer_id: str,
        event_type: str,
        event_id: str,
        payload_json: str,
        now: str,
    ) -> int:
        with self._connect() as con:
            r = con.execute(
                "INSERT INTO webhook_deliveries(subscription_id, customer_id, event_type, event_id, payload_json, status, attempt_count, next_attempt_at, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,'queued',0,%s,%s,%s) RETURNING delivery_id",
                (subscription_id, customer_id, event_type, event_id, payload_json, now, now, now),
            ).fetchone()
            return int(r[0]) if r else 0

    def list_due_webhook_deliveries(self, *, now: str, limit: int = 50) -> list[dict[str, Any]]:
        limit_n = max(1, min(int(limit), 500))
        with self._connect() as con:
            rows = con.execute(
                "SELECT delivery_id, subscription_id, customer_id, event_type, event_id, payload_json, status, attempt_count "
                "FROM webhook_deliveries WHERE status IN ('queued','retry') AND next_attempt_at <= %s "
                "ORDER BY next_attempt_at ASC LIMIT %s",
                (now, limit_n),
            ).fetchall()
        return [
            {
                "delivery_id": int(r[0]),
                "subscription_id": str(r[1]),
                "customer_id": str(r[2]),
                "event_type": str(r[3]),
                "event_id": str(r[4]),
                "payload_json": str(r[5]),
                "status": str(r[6]),
                "attempt_count": int(r[7]),
            }
            for r in rows
        ]

    def get_webhook_delivery(self, *, delivery_id: int) -> dict[str, Any] | None:
        with self._connect() as con:
            r = con.execute(
                "SELECT delivery_id, subscription_id, customer_id, event_type, event_id, payload_json, status, attempt_count, next_attempt_at "
                "FROM webhook_deliveries WHERE delivery_id=%s",
                (int(delivery_id),),
            ).fetchone()
            if r is None:
                return None
        return {
            "delivery_id": int(r[0]),
            "subscription_id": str(r[1]),
            "customer_id": str(r[2]),
            "event_type": str(r[3]),
            "event_id": str(r[4]),
            "payload_json": str(r[5]),
            "status": str(r[6]),
            "attempt_count": int(r[7]),
            "next_attempt_at": str(r[8]),
        }

    def mark_webhook_delivery_success(
        self, *, delivery_id: int, now: str, status_code: int
    ) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE webhook_deliveries SET status='delivered', last_attempt_at=%s, last_status_code=%s, updated_at=%s WHERE delivery_id=%s",
                (now, int(status_code), now, int(delivery_id)),
            )

    def mark_webhook_delivery_retry(
        self,
        *,
        delivery_id: int,
        now: str,
        next_attempt_at: str,
        status_code: int | None,
        error: str | None,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE webhook_deliveries SET status='retry', attempt_count=attempt_count+1, next_attempt_at=%s, last_attempt_at=%s, last_status_code=%s, last_error=%s, updated_at=%s WHERE delivery_id=%s",
                (
                    next_attempt_at,
                    now,
                    int(status_code) if status_code is not None else None,
                    error,
                    now,
                    int(delivery_id),
                ),
            )

    def mark_webhook_delivery_failed(
        self, *, delivery_id: int, now: str, error: str | None
    ) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE webhook_deliveries SET status='failed', last_attempt_at=%s, last_error=%s, updated_at=%s WHERE delivery_id=%s",
                (now, error, now, int(delivery_id)),
            )

    # --- jobs queue (pilot) ---

    def enqueue_job(
        self, *, job_type: str, job_key: str | None, payload_json: str, run_at: str, now: str
    ) -> int:
        with self._connect() as con:
            r = con.execute(
                "INSERT INTO jobs(job_type, job_key, payload_json, status, attempt_count, run_at, created_at, updated_at) "
                "VALUES (%s,%s,%s,'queued',0,%s,%s,%s) RETURNING job_id",
                (job_type, job_key, payload_json, run_at, now, now),
            ).fetchone()
            return int(r[0]) if r else 0

    def claim_due_jobs(
        self, *, now: str, locked_until: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        limit_n = max(1, min(int(limit), 500))
        with self._connect() as con:
            rows = con.execute(
                """
                WITH picked AS (
                  SELECT job_id
                  FROM jobs
                  WHERE status IN ('queued','retry')
                    AND run_at <= %s
                    AND (locked_until IS NULL OR locked_until <= %s)
                  ORDER BY run_at ASC
                  LIMIT %s
                  FOR UPDATE SKIP LOCKED
                )
                UPDATE jobs j
                SET locked_until = %s,
                    updated_at = %s
                FROM picked
                WHERE j.job_id = picked.job_id
                RETURNING j.job_id, j.job_type, j.job_key, j.payload_json, j.status, j.attempt_count
                """,
                (now, now, limit_n, locked_until, now),
            ).fetchall()
        return [
            {
                "job_id": int(r[0]),
                "job_type": str(r[1]),
                "job_key": str(r[2]) if r[2] is not None else "",
                "payload_json": str(r[3]),
                "status": str(r[4]),
                "attempt_count": int(r[5]),
            }
            for r in rows
        ]

    def delete_pending_jobs(self, *, job_type: str, job_key: str) -> int:
        with self._connect() as con:
            r = con.execute(
                "DELETE FROM jobs WHERE job_type=%s AND job_key=%s AND status IN ('queued','retry')",
                (job_type, job_key),
            )
            return int(getattr(r, "rowcount", 0) or 0)

    def job_counts_by_status(self) -> dict[str, int]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT status, COUNT(*)::INT AS n FROM jobs GROUP BY status ORDER BY status"
            ).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    def job_locked_count(self, *, now: str) -> int:
        with self._connect() as con:
            row = con.execute(
                "SELECT COUNT(*)::INT AS n FROM jobs WHERE locked_until IS NOT NULL AND locked_until > %s",
                (now,),
            ).fetchone()
        return int(row[0]) if row else 0

    def job_lag_seconds(self, *, now: str) -> int:
        with self._connect() as con:
            row = con.execute(
                "SELECT MIN(run_at) FROM jobs WHERE status IN ('queued','retry')"
            ).fetchone()
        min_run_at = str(row[0]) if row and row[0] is not None else ""
        if not min_run_at:
            return 0
        try:
            now_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
            due_dt = datetime.fromisoformat(min_run_at.replace("Z", "+00:00"))
            lag = int((now_dt - due_dt).total_seconds())
            return max(0, lag)
        except Exception:  # noqa: BLE001
            return 0

    def list_failed_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        limit_n = max(1, min(int(limit), 500))
        with self._connect() as con:
            rows = con.execute(
                "SELECT job_id, job_type, job_key, status, attempt_count, run_at, last_error, created_at, updated_at "
                "FROM jobs WHERE status='failed' ORDER BY updated_at DESC LIMIT %s",
                (limit_n,),
            ).fetchall()
        return [
            {
                "job_id": int(r[0]),
                "job_type": str(r[1]),
                "job_key": str(r[2]) if r[2] is not None else "",
                "status": str(r[3]),
                "attempt_count": int(r[4]),
                "run_at": str(r[5]),
                "last_error": str(r[6]) if r[6] is not None else None,
                "created_at": str(r[7]),
                "updated_at": str(r[8]),
            }
            for r in rows
        ]

    def get_oldest_pending_job(self) -> dict[str, Any] | None:
        with self._connect() as con:
            r = con.execute(
                "SELECT job_id, job_type, job_key, status, attempt_count, run_at, last_error, created_at, updated_at "
                "FROM jobs WHERE status IN ('queued','retry') ORDER BY run_at ASC LIMIT 1"
            ).fetchone()
        if not r:
            return None
        return {
            "job_id": int(r[0]),
            "job_type": str(r[1]),
            "job_key": str(r[2]) if r[2] is not None else "",
            "status": str(r[3]),
            "attempt_count": int(r[4]),
            "run_at": str(r[5]),
            "last_error": str(r[6]) if r[6] is not None else None,
            "created_at": str(r[7]),
            "updated_at": str(r[8]),
        }

    def get_idempotency_record(
        self, *, customer_id: str, scope: str, idem_key: str
    ) -> dict[str, Any] | None:
        with self._connect() as con:
            r = con.execute(
                "SELECT customer_id, scope, idem_key, request_sha256, status_code, response_json, created_at "
                "FROM idempotency_keys WHERE customer_id=%s AND scope=%s AND idem_key=%s",
                (customer_id, scope, idem_key),
            ).fetchone()
        if not r:
            return None
        return {
            "customer_id": str(r[0]),
            "scope": str(r[1]),
            "idem_key": str(r[2]),
            "request_sha256": str(r[3]),
            "status_code": int(r[4]),
            "response_json": str(r[5]),
            "created_at": str(r[6]),
        }

    def put_idempotency_record(
        self,
        *,
        customer_id: str,
        scope: str,
        idem_key: str,
        request_sha256: str,
        status_code: int,
        response_json: str,
        created_at: str,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO idempotency_keys(customer_id, scope, idem_key, request_sha256, status_code, response_json, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    customer_id,
                    scope,
                    idem_key,
                    request_sha256,
                    int(status_code),
                    response_json,
                    created_at,
                ),
            )

    def delete_jobs_before(self, *, cutoff_ts: str, statuses: list[str]) -> int:
        st = [str(s) for s in statuses if str(s)]
        if not st:
            return 0
        with self._connect() as con:
            r = con.execute(
                "DELETE FROM jobs WHERE updated_at < %s AND status = ANY(%s)",
                (cutoff_ts, st),
            )
            return int(getattr(r, "rowcount", 0) or 0)

    def mark_job_success(self, *, job_id: int, now: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE jobs SET status='done', updated_at=%s WHERE job_id=%s",
                (now, int(job_id)),
            )

    def mark_job_retry(self, *, job_id: int, now: str, run_at: str, error: str | None) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE jobs SET status='retry', attempt_count=attempt_count+1, run_at=%s, last_error=%s, updated_at=%s WHERE job_id=%s",
                (run_at, error, now, int(job_id)),
            )

    def mark_job_failed(self, *, job_id: int, now: str, error: str | None) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE jobs SET status='failed', last_error=%s, updated_at=%s WHERE job_id=%s",
                (error, now, int(job_id)),
            )

    def webhook_delivery_counts(self) -> dict[str, int]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT status, COUNT(*) FROM webhook_deliveries GROUP BY status"
            ).fetchall()
        return {str(r[0]): int(r[1]) for r in rows}

    def list_failed_webhook_deliveries(self, *, limit: int = 100) -> list[dict[str, Any]]:
        limit_n = max(1, min(500, int(limit)))
        with self._connect() as con:
            rows = con.execute(
                "SELECT delivery_id, subscription_id, customer_id, event_type, event_id, status, attempt_count, next_attempt_at, last_attempt_at, last_status_code, last_error, created_at, updated_at "
                "FROM webhook_deliveries WHERE status='failed' ORDER BY updated_at DESC LIMIT %s",
                (limit_n,),
            ).fetchall()
        cols = [
            "delivery_id",
            "subscription_id",
            "customer_id",
            "event_type",
            "event_id",
            "status",
            "attempt_count",
            "next_attempt_at",
            "last_attempt_at",
            "last_status_code",
            "last_error",
            "created_at",
            "updated_at",
        ]
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append({cols[i]: r[i] for i in range(len(cols))})
        return out
