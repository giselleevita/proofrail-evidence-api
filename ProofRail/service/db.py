from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

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
                con.execute("ALTER TABLE api_keys ADD COLUMN scopes TEXT NOT NULL DEFAULT 'write:screen,read:evidence'")
            usage_cols = {row["name"] for row in con.execute("PRAGMA table_info(usage_events)").fetchall()}
            if "request_id" not in usage_cols:
                con.execute("ALTER TABLE usage_events ADD COLUMN request_id TEXT NULL")

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
                (ts, api_key_id, customer_id, route, int(status_code), float(latency_ms), request_id),
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

