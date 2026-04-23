import json
import tempfile
import unittest
from pathlib import Path

from ProofRail.service.storage import EvidenceStore, canonical_json_bytes, sha256_hex


class TestStorageIntegrity(unittest.TestCase):
    def test_get_pack_rejects_tampered_payload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = EvidenceStore(root)

            pack = {"schema_version": "1", "x": 1}
            pack_id = sha256_hex(canonical_json_bytes(pack))
            path = store.evidence_pack_path(pack_id)
            path.parent.mkdir(parents=True, exist_ok=True)

            tampered = json.dumps({"schema_version": "1", "x": 2}).encode("utf-8")
            path.write_bytes(tampered)

            with self.assertRaises(ValueError) as ctx:
                store.get_pack(pack_id)
            self.assertEqual(str(ctx.exception), "evidence_pack_integrity_failed")
