import concurrent.futures
import tempfile
import unittest
from pathlib import Path

from ProofRail.service.db import DbConfig, ProofRailDb
from ProofRail.service.utils import utc_now_iso


class TestSqliteConcurrency(unittest.TestCase):
    def test_concurrent_ensure_customer(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "c.sqlite3"
            db = ProofRailDb(DbConfig(path=p))
            now = utc_now_iso()

            def touch(i: int) -> None:
                db.ensure_customer(f"tenant-{i % 7}", now)

            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
                list(ex.map(touch, range(60)))
