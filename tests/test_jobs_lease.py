import tempfile
import unittest
from pathlib import Path

from ProofRail.service.db import DbConfig, ProofRailDb
from ProofRail.service.utils import utc_now_iso


class TestJobStaleLeases(unittest.TestCase):
    def test_release_expired_clears_stale_locked_until(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.sqlite3"
            db = ProofRailDb(DbConfig(path=p))
            now = utc_now_iso()
            past = "2020-01-01T00:00:00Z"
            jid = db.enqueue_job(
                job_type="webhook_delivery",
                job_key="k1",
                payload_json="{}",
                run_at=now,
                now=now,
            )
            with db._connect() as con:
                con.execute(
                    "UPDATE jobs SET locked_until = ?, status = 'queued' WHERE job_id = ?",
                    (past, int(jid)),
                )
            self.assertGreater(db.count_stale_job_leases(now=now), 0)
            n = db.release_expired_job_leases(now=now)
            self.assertGreaterEqual(n, 1)
            self.assertEqual(db.count_stale_job_leases(now=now), 0)
