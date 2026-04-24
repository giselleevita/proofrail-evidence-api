from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS customers (
  customer_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
  api_key_id TEXT PRIMARY KEY,
  customer_id TEXT NOT NULL,
  key_sha256 TEXT NOT NULL UNIQUE,
  scopes TEXT NOT NULL,
  created_at TEXT NOT NULL,
  revoked_at TEXT NULL,
  FOREIGN KEY(customer_id) REFERENCES customers(customer_id)
);

CREATE INDEX IF NOT EXISTS idx_api_keys_customer_id ON api_keys(customer_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_sha ON api_keys(key_sha256);

CREATE TABLE IF NOT EXISTS usage_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  api_key_id TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  route TEXT NOT NULL,
  status_code INTEGER NOT NULL,
  latency_ms REAL NOT NULL,
  request_id TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_events_ts ON usage_events(ts);
CREATE INDEX IF NOT EXISTS idx_usage_events_key ON usage_events(api_key_id);
CREATE INDEX IF NOT EXISTS idx_usage_events_customer_ts ON usage_events(customer_id, ts);

CREATE TABLE IF NOT EXISTS plans (
  customer_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  rpm INTEGER NOT NULL,
  evidence_retention_days INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
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
  screening_id TEXT PRIMARY KEY,
  decided_at TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  evidence_pack_id TEXT NOT NULL,
  outcome TEXT NOT NULL,
  note TEXT NULL,
  FOREIGN KEY(screening_id) REFERENCES screenings(screening_id)
);

CREATE TABLE IF NOT EXISTS cases (
  case_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  customer_id TEXT NOT NULL,
  screening_id TEXT NOT NULL,
  evidence_pack_id TEXT NOT NULL,
  status TEXT NOT NULL,
  assignee TEXT NULL,
  FOREIGN KEY(screening_id) REFERENCES screenings(screening_id)
);

CREATE INDEX IF NOT EXISTS idx_cases_customer_updated ON cases(customer_id, updated_at);

CREATE TABLE IF NOT EXISTS case_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  case_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,
  event_type TEXT NOT NULL,
  note TEXT NULL,
  FOREIGN KEY(case_id) REFERENCES cases(case_id)
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
  delivery_id INTEGER PRIMARY KEY AUTOINCREMENT,
  subscription_id TEXT NOT NULL,
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
  updated_at TEXT NOT NULL,
  FOREIGN KEY(subscription_id) REFERENCES webhook_subscriptions(subscription_id)
);

CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_due ON webhook_deliveries(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_customer ON webhook_deliveries(customer_id, created_at);

CREATE TABLE IF NOT EXISTS jobs (
  job_id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_type TEXT NOT NULL,
  job_key TEXT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  run_at TEXT NOT NULL,
  last_error TEXT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(status, run_at);
"""


@dataclass(frozen=True)
class DbConfig:
    path: Path


class ProofRailDb:
    def __init__(self, cfg: DbConfig) -> None:
        self.cfg = cfg
        self.cfg.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        # Prefer explicit timeouts + WAL for better concurrency behavior under concurrent writes.
        con = sqlite3.connect(self.cfg.path, timeout=30.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=30000")
        return con

    def _init(self) -> None:
        with self._connect() as con:
            con.executescript(SCHEMA_SQL)
            # Back-compat migration for older SQLite files (best-effort).
            cols = {row["name"] for row in con.execute("PRAGMA table_info(api_keys)").fetchall()}
            if "scopes" not in cols:
                con.execute(
                    "ALTER TABLE api_keys ADD COLUMN scopes TEXT NOT NULL DEFAULT 'write:screen,read:evidence'"
                )
            usage_cols = {
                row["name"] for row in con.execute("PRAGMA table_info(usage_events)").fetchall()
            }
            if "request_id" not in usage_cols:
                con.execute("ALTER TABLE usage_events ADD COLUMN request_id TEXT NULL")

            # Back-compat migration for audit_events (best-effort).
            if (
                con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_events'"
                ).fetchone()
                is None
            ):
                con.execute(
                    "CREATE TABLE IF NOT EXISTS audit_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, request_id TEXT NULL, details_json TEXT NULL)"
                )
                con.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_ts ON audit_events(ts)")

            case_cols = {row["name"] for row in con.execute("PRAGMA table_info(cases)").fetchall()}
            if "assignee" not in case_cols:
                con.execute("ALTER TABLE cases ADD COLUMN assignee TEXT NULL")

            # Back-compat migration for webhook tables (best-effort).
            if (
                con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='webhook_subscriptions'"
                ).fetchone()
                is None
            ):
                con.execute(
                    "CREATE TABLE IF NOT EXISTS webhook_subscriptions (subscription_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, url TEXT NOT NULL, secret TEXT NOT NULL, events TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_webhook_subscriptions_customer ON webhook_subscriptions(customer_id)"
                )
            if (
                con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='webhook_deliveries'"
                ).fetchone()
                is None
            ):
                con.execute(
                    "CREATE TABLE IF NOT EXISTS webhook_deliveries (delivery_id INTEGER PRIMARY KEY AUTOINCREMENT, subscription_id TEXT NOT NULL, customer_id TEXT NOT NULL, event_type TEXT NOT NULL, event_id TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL, last_attempt_at TEXT NULL, last_status_code INTEGER NULL, last_error TEXT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, FOREIGN KEY(subscription_id) REFERENCES webhook_subscriptions(subscription_id))"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_due ON webhook_deliveries(status, next_attempt_at)"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_customer ON webhook_deliveries(customer_id, created_at)"
                )

            # Jobs table (pilot queue). Back-compat migration for older DBs.
            if (
                con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='jobs'"
                ).fetchone()
                is None
            ):
                con.execute(
                    "CREATE TABLE IF NOT EXISTS jobs (job_id INTEGER PRIMARY KEY AUTOINCREMENT, job_type TEXT NOT NULL, job_key TEXT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0, run_at TEXT NOT NULL, last_error TEXT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
                )
                con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(status, run_at)")

            # Jobs leasing (safe migration): add locked_until column + index if missing.
            cols = {r["name"] for r in con.execute("PRAGMA table_info(jobs)").fetchall()}
            if "job_key" not in cols:
                con.execute("ALTER TABLE jobs ADD COLUMN job_key TEXT NULL")
            if "locked_until" not in cols:
                con.execute("ALTER TABLE jobs ADD COLUMN locked_until TEXT NULL")
            con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_locked_until ON jobs(locked_until)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_jobs_key ON jobs(job_type, job_key)")

    def ensure_customer(self, customer_id: str, created_at: str) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT OR IGNORE INTO customers(customer_id, created_at) VALUES (?, ?)",
                (customer_id, created_at),
            )

    def create_api_key(
        self, api_key_id: str, customer_id: str, key_sha256: str, scopes: str, created_at: str
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO api_keys(api_key_id, customer_id, key_sha256, scopes, created_at) VALUES (?, ?, ?, ?, ?)",
                (api_key_id, customer_id, key_sha256, scopes, created_at),
            )

    def resolve_api_key(self, key_sha256: str) -> tuple[str, str, str] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT api_key_id, customer_id, scopes FROM api_keys WHERE key_sha256 = ? AND revoked_at IS NULL",
                (key_sha256,),
            ).fetchone()
            if row is None:
                return None
            return (str(row["api_key_id"]), str(row["customer_id"]), str(row["scopes"]))

    def revoke_api_key(self, api_key_id: str, revoked_at: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE api_key_id = ?",
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
                "INSERT INTO audit_events(ts, actor, action, request_id, details_json) VALUES (?, ?, ?, ?, ?)",
                (ts, actor, action, request_id, details_json),
            )

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
                "INSERT OR REPLACE INTO screenings(screening_id, created_at, customer_id, evidence_pack_id, list_version) "
                "VALUES (?, ?, ?, ?, ?)",
                (screening_id, created_at, customer_id, evidence_pack_id, list_version),
            )

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
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(case_id) DO UPDATE SET updated_at=excluded.updated_at, status=excluded.status, evidence_pack_id=excluded.evidence_pack_id, assignee=COALESCE(excluded.assignee, cases.assignee)",
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
            row = con.execute(
                "SELECT case_id, created_at, updated_at, customer_id, screening_id, evidence_pack_id, status, assignee FROM cases WHERE case_id = ?",
                (case_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "case_id": str(row["case_id"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "customer_id": str(row["customer_id"]),
            "screening_id": str(row["screening_id"]),
            "evidence_pack_id": str(row["evidence_pack_id"]),
            "status": str(row["status"]),
            "assignee": str(row["assignee"]) if row["assignee"] is not None else "",
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
        with self._connect() as con:
            if status and assignee:
                rows = con.execute(
                    "SELECT case_id, created_at, updated_at, customer_id, screening_id, evidence_pack_id, status, assignee "
                    "FROM cases WHERE customer_id = ? AND status = ? AND assignee = ? ORDER BY updated_at DESC LIMIT ?",
                    (customer_id, status, assignee, limit_n),
                ).fetchall()
            elif status:
                rows = con.execute(
                    "SELECT case_id, created_at, updated_at, customer_id, screening_id, evidence_pack_id, status, assignee "
                    "FROM cases WHERE customer_id = ? AND status = ? ORDER BY updated_at DESC LIMIT ?",
                    (customer_id, status, limit_n),
                ).fetchall()
            elif assignee:
                rows = con.execute(
                    "SELECT case_id, created_at, updated_at, customer_id, screening_id, evidence_pack_id, status, assignee "
                    "FROM cases WHERE customer_id = ? AND assignee = ? ORDER BY updated_at DESC LIMIT ?",
                    (customer_id, assignee, limit_n),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT case_id, created_at, updated_at, customer_id, screening_id, evidence_pack_id, status, assignee "
                    "FROM cases WHERE customer_id = ? ORDER BY updated_at DESC LIMIT ?",
                    (customer_id, limit_n),
                ).fetchall()
        return [
            {
                "case_id": str(r["case_id"]),
                "created_at": str(r["created_at"]),
                "updated_at": str(r["updated_at"]),
                "customer_id": str(r["customer_id"]),
                "screening_id": str(r["screening_id"]),
                "evidence_pack_id": str(r["evidence_pack_id"]),
                "status": str(r["status"]),
                "assignee": str(r["assignee"]) if r["assignee"] is not None else "",
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
                "INSERT INTO case_events(case_id, ts, actor, event_type, note) VALUES (?, ?, ?, ?, ?)",
                (case_id, ts, actor, event_type, note),
            )

    def list_case_events(self, *, case_id: str, limit: int = 200) -> list[dict[str, str]]:
        limit_n = max(1, min(500, int(limit)))
        with self._connect() as con:
            rows = con.execute(
                "SELECT ts, actor, event_type, note FROM case_events WHERE case_id = ? ORDER BY ts ASC LIMIT ?",
                (case_id, limit_n),
            ).fetchall()
        return [
            {
                "ts": str(r["ts"]),
                "actor": str(r["actor"]),
                "event_type": str(r["event_type"]),
                "note": str(r["note"]) if r["note"] is not None else "",
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
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
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
                "FROM webhook_subscriptions WHERE customer_id = ? ORDER BY created_at DESC",
                (customer_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(
                {
                    "subscription_id": str(r["subscription_id"]),
                    "customer_id": str(r["customer_id"]),
                    "url": str(r["url"]),
                    "secret": str(r["secret"]),
                    "events": json.loads(str(r["events"])) if r["events"] else [],
                    "active": bool(int(r["active"])),
                    "created_at": str(r["created_at"]),
                }
            )
        return out

    def get_webhook_subscription(self, *, subscription_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            r = con.execute(
                "SELECT subscription_id, customer_id, url, secret, events, active, created_at "
                "FROM webhook_subscriptions WHERE subscription_id = ?",
                (subscription_id,),
            ).fetchone()
            if r is None:
                return None
        return {
            "subscription_id": str(r["subscription_id"]),
            "customer_id": str(r["customer_id"]),
            "url": str(r["url"]),
            "secret": str(r["secret"]),
            "events": json.loads(str(r["events"])) if r["events"] else [],
            "active": bool(int(r["active"])),
            "created_at": str(r["created_at"]),
        }

    def delete_webhook_subscription(self, *, subscription_id: str, customer_id: str) -> bool:
        with self._connect() as con:
            # Best-effort cleanup for existing deliveries (FK constraint).
            con.execute(
                "DELETE FROM webhook_deliveries WHERE subscription_id = ? AND customer_id = ?",
                (subscription_id, customer_id),
            )
            cur = con.execute(
                "DELETE FROM webhook_subscriptions WHERE subscription_id = ? AND customer_id = ?",
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
            cur = con.execute(
                "INSERT INTO webhook_deliveries(subscription_id, customer_id, event_type, event_id, payload_json, status, attempt_count, next_attempt_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?)",
                (subscription_id, customer_id, event_type, event_id, payload_json, now, now, now),
            )
            return int(cur.lastrowid)

    def list_due_webhook_deliveries(self, *, now: str, limit: int = 50) -> list[dict[str, Any]]:
        limit_n = max(1, min(int(limit), 500))
        with self._connect() as con:
            rows = con.execute(
                "SELECT delivery_id, subscription_id, customer_id, event_type, event_id, payload_json, status, attempt_count "
                "FROM webhook_deliveries WHERE status IN ('queued','retry') AND next_attempt_at <= ? "
                "ORDER BY next_attempt_at ASC LIMIT ?",
                (now, limit_n),
            ).fetchall()
        return [
            {
                "delivery_id": int(r["delivery_id"]),
                "subscription_id": str(r["subscription_id"]),
                "customer_id": str(r["customer_id"]),
                "event_type": str(r["event_type"]),
                "event_id": str(r["event_id"]),
                "payload_json": str(r["payload_json"]),
                "status": str(r["status"]),
                "attempt_count": int(r["attempt_count"]),
            }
            for r in rows
        ]

    def get_webhook_delivery(self, *, delivery_id: int) -> dict[str, Any] | None:
        with self._connect() as con:
            r = con.execute(
                "SELECT delivery_id, subscription_id, customer_id, event_type, event_id, payload_json, status, attempt_count, next_attempt_at "
                "FROM webhook_deliveries WHERE delivery_id = ?",
                (int(delivery_id),),
            ).fetchone()
            if r is None:
                return None
        return {
            "delivery_id": int(r["delivery_id"]),
            "subscription_id": str(r["subscription_id"]),
            "customer_id": str(r["customer_id"]),
            "event_type": str(r["event_type"]),
            "event_id": str(r["event_id"]),
            "payload_json": str(r["payload_json"]),
            "status": str(r["status"]),
            "attempt_count": int(r["attempt_count"]),
            "next_attempt_at": str(r["next_attempt_at"]),
        }

    def mark_webhook_delivery_success(
        self, *, delivery_id: int, now: str, status_code: int
    ) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE webhook_deliveries SET status='delivered', last_attempt_at=?, last_status_code=?, updated_at=? WHERE delivery_id=?",
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
                "UPDATE webhook_deliveries SET status='retry', attempt_count=attempt_count+1, next_attempt_at=?, last_attempt_at=?, last_status_code=?, last_error=?, updated_at=? WHERE delivery_id=?",
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
                "UPDATE webhook_deliveries SET status='failed', last_attempt_at=?, last_error=?, updated_at=? WHERE delivery_id=?",
                (now, error, now, int(delivery_id)),
            )

    def webhook_delivery_counts(self) -> dict[str, int]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT status, COUNT(*) AS n FROM webhook_deliveries GROUP BY status"
            ).fetchall()
        out: dict[str, int] = {}
        for r in rows:
            out[str(r["status"])] = int(r["n"])
        return out

    def list_failed_webhook_deliveries(self, *, limit: int = 100) -> list[dict[str, Any]]:
        limit_n = max(1, min(500, int(limit)))
        with self._connect() as con:
            rows = con.execute(
                "SELECT delivery_id, subscription_id, customer_id, event_type, event_id, status, attempt_count, next_attempt_at, last_attempt_at, last_status_code, last_error, created_at, updated_at "
                "FROM webhook_deliveries WHERE status='failed' ORDER BY updated_at DESC LIMIT ?",
                (limit_n,),
            ).fetchall()
        return [
            {k: (r[k] if r[k] is not None else None) for k in r.keys()}  # type: ignore[attr-defined]
            for r in rows
        ]

    # --- jobs queue (pilot) ---

    def enqueue_job(
        self, *, job_type: str, job_key: str | None, payload_json: str, run_at: str, now: str
    ) -> int:
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO jobs(job_type, job_key, payload_json, status, attempt_count, run_at, created_at, updated_at) "
                "VALUES (?, ?, ?, 'queued', 0, ?, ?, ?)",
                (job_type, job_key, payload_json, run_at, now, now),
            )
            return int(cur.lastrowid)

    def claim_due_jobs(
        self, *, now: str, locked_until: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        limit_n = max(1, min(int(limit), 500))
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute(
                "SELECT job_id, job_type, job_key, payload_json, status, attempt_count "
                "FROM jobs "
                "WHERE status IN ('queued','retry') AND run_at <= ? AND (locked_until IS NULL OR locked_until <= ?) "
                "ORDER BY run_at ASC LIMIT ?",
                (now, now, limit_n),
            ).fetchall()
            job_ids = [int(r["job_id"]) for r in rows]
            if job_ids:
                con.execute(
                    f"UPDATE jobs SET locked_until = ?, updated_at = ? WHERE job_id IN ({','.join(['?'] * len(job_ids))})",
                    (locked_until, now, *job_ids),
                )
        return [
            {
                "job_id": int(r["job_id"]),
                "job_type": str(r["job_type"]),
                "job_key": str(r["job_key"]) if r["job_key"] is not None else "",
                "payload_json": str(r["payload_json"]),
                "status": str(r["status"]),
                "attempt_count": int(r["attempt_count"]),
            }
            for r in rows
        ]

    def delete_pending_jobs(self, *, job_type: str, job_key: str) -> int:
        with self._connect() as con:
            cur = con.execute(
                "DELETE FROM jobs WHERE job_type = ? AND job_key = ? AND status IN ('queued','retry')",
                (job_type, job_key),
            )
            return int(cur.rowcount or 0)

    def job_counts_by_status(self) -> dict[str, int]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status ORDER BY status"
            ).fetchall()
        return {str(r["status"]): int(r["n"]) for r in rows}

    def job_locked_count(self, *, now: str) -> int:
        with self._connect() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE locked_until IS NOT NULL AND locked_until > ?",
                (now,),
            ).fetchone()
        return int(row["n"] if row is not None else 0)

    def job_lag_seconds(self, *, now: str) -> int:
        with self._connect() as con:
            row = con.execute(
                "SELECT MIN(run_at) AS min_run_at FROM jobs WHERE status IN ('queued','retry')"
            ).fetchone()
        min_run_at = str(row["min_run_at"]) if row and row["min_run_at"] is not None else ""
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
                "FROM jobs WHERE status = 'failed' ORDER BY updated_at DESC LIMIT ?",
                (limit_n,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_jobs_before(self, *, ts_exclusive: str, statuses: list[str]) -> int:
        st = [str(s) for s in statuses if str(s)]
        if not st:
            return 0
        with self._connect() as con:
            cur = con.execute(
                f"DELETE FROM jobs WHERE updated_at < ? AND status IN ({','.join(['?'] * len(st))})",
                (ts_exclusive, *st),
            )
            return int(cur.rowcount or 0)

    def mark_job_success(self, *, job_id: int, now: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE jobs SET status='done', updated_at=? WHERE job_id=?",
                (now, int(job_id)),
            )

    def mark_job_retry(self, *, job_id: int, now: str, run_at: str, error: str | None) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE jobs SET status='retry', attempt_count=attempt_count+1, run_at=?, last_error=?, updated_at=? WHERE job_id=?",
                (run_at, error, now, int(job_id)),
            )

    def mark_job_failed(self, *, job_id: int, now: str, error: str | None) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE jobs SET status='failed', last_error=?, updated_at=? WHERE job_id=?",
                (error, now, int(job_id)),
            )

    def get_screening(self, screening_id: str) -> dict[str, str] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT screening_id, created_at, customer_id, evidence_pack_id, list_version FROM screenings WHERE screening_id = ?",
                (screening_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "screening_id": str(row["screening_id"]),
            "created_at": str(row["created_at"]),
            "customer_id": str(row["customer_id"]),
            "evidence_pack_id": str(row["evidence_pack_id"]),
            "list_version": str(row["list_version"]),
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
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(screening_id) DO UPDATE SET decided_at=excluded.decided_at, customer_id=excluded.customer_id, evidence_pack_id=excluded.evidence_pack_id, outcome=excluded.outcome, note=excluded.note",
                (screening_id, decided_at, customer_id, evidence_pack_id, outcome, note),
            )

    def get_screening_review_decision_by_evidence_pack(
        self, *, customer_id: str, evidence_pack_id: str
    ) -> dict[str, str] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT screening_id, decided_at, outcome, note FROM screening_review_decisions WHERE customer_id = ? AND evidence_pack_id = ?",
                (customer_id, evidence_pack_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "screening_id": str(row["screening_id"]),
            "decided_at": str(row["decided_at"]),
            "outcome": str(row["outcome"]),
            "note": str(row["note"]) if row["note"] is not None else "",
        }

    def insert_usage_event(
        self,
        *,
        ts: str,
        api_key_id: str,
        customer_id: str,
        route: str,
        status_code: int,
        latency_ms: float,
        request_id: str | None = None,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO usage_events(ts, api_key_id, customer_id, route, status_code, latency_ms, request_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    api_key_id,
                    customer_id,
                    route,
                    int(status_code),
                    float(latency_ms),
                    request_id,
                ),
            )

    def insert_usage_events(self, events: list[dict[str, object]]) -> None:
        if not events:
            return
        with self._connect() as con:
            con.executemany(
                "INSERT INTO usage_events(ts, api_key_id, customer_id, route, status_code, latency_ms, request_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        str(e["ts"]),
                        str(e["api_key_id"]),
                        str(e["customer_id"]),
                        str(e["route"]),
                        int(e["status_code"]),
                        float(e["latency_ms"]),
                        e.get("request_id"),
                    )
                    for e in events
                ],
            )

    def delete_usage_events_before(self, ts_exclusive: str) -> int:
        with self._connect() as con:
            cur = con.execute("DELETE FROM usage_events WHERE ts < ?", (ts_exclusive,))
            return int(cur.rowcount or 0)

    def usage_summary(self, customer_id: str, since_ts: str) -> dict[str, int]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT route, COUNT(*) AS c FROM usage_events WHERE customer_id = ? AND ts >= ? GROUP BY route",
                (customer_id, since_ts),
            ).fetchall()
        return {str(r["route"]): int(r["c"]) for r in rows}

    def upsert_plan(
        self,
        *,
        customer_id: str,
        plan_id: str,
        rpm: int,
        evidence_retention_days: int,
        created_at: str,
    ) -> None:
        with self._connect() as con:
            con.execute(
                "INSERT INTO plans(customer_id, plan_id, rpm, evidence_retention_days, created_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(customer_id) DO UPDATE SET plan_id=excluded.plan_id, rpm=excluded.rpm, evidence_retention_days=excluded.evidence_retention_days",
                (customer_id, plan_id, int(rpm), int(evidence_retention_days), created_at),
            )

    def get_plan(self, customer_id: str) -> dict[str, int | str] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT plan_id, rpm, evidence_retention_days FROM plans WHERE customer_id = ?",
                (customer_id,),
            ).fetchone()
            if row is None:
                return None
            return {
                "plan_id": str(row["plan_id"]),
                "rpm": int(row["rpm"]),
                "evidence_retention_days": int(row["evidence_retention_days"]),
            }
