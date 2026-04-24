import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from ProofRail.service.app import create_app


class TestOpenApiSnapshot(unittest.TestCase):
    def test_openapi_snapshot_matches(self) -> None:
        client = TestClient(create_app())
        current_full = client.get("/openapi.json").json()
        current_full.pop("servers", None)

        def filter_paths(prefix: str) -> dict[str, object]:
            paths: dict[str, object] = dict(current_full.get("paths") or {})
            return {
                p: v
                for p, v in paths.items()
                if p.startswith(prefix) or p in ("/healthz", "/readyz")
            }

        v1 = dict(current_full)
        v1["paths"] = filter_paths("/v1/")
        v2 = dict(current_full)
        v2["paths"] = filter_paths("/v2/")

        v1_path = Path(__file__).with_name("openapi.v1.snapshot.json")
        v2_path = Path(__file__).with_name("openapi.v2.snapshot.json")
        v1_snap = json.loads(v1_path.read_text(encoding="utf-8"))
        v2_snap = json.loads(v2_path.read_text(encoding="utf-8"))

        self.assertEqual(v1_snap, v1)
        self.assertEqual(v2_snap, v2)
