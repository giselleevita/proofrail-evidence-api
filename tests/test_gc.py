import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ProofRail.service.storage import EvidenceStore, canonical_json_bytes, sha256_hex


class TestGc(unittest.TestCase):
    def test_delete_packs_before(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = EvidenceStore(Path(td))
            now = datetime.now(UTC).replace(microsecond=0)

            old_pack = {
                "schema_version": "1",
                "created_at": (now - timedelta(days=10)).isoformat().replace("+00:00", "Z"),
                "customer_id": "c1",
            }
            new_pack = {
                "schema_version": "1",
                "created_at": (now - timedelta(days=1)).isoformat().replace("+00:00", "Z"),
                "customer_id": "c1",
            }

            old_id = sha256_hex(canonical_json_bytes(old_pack))
            new_id = sha256_hex(canonical_json_bytes(new_pack))

            store.evidence_pack_path(old_id).write_bytes(canonical_json_bytes(old_pack))
            store.evidence_pack_path(new_id).write_bytes(canonical_json_bytes(new_pack))

            deleted = store.delete_packs_before(
                cutoff=now - timedelta(days=5), customer_id="c1", dry_run=False
            )
            self.assertEqual(deleted, 1)
            self.assertFalse(store.has_pack(old_id))
            self.assertTrue(store.has_pack(new_id))
