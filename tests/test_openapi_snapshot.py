import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from ProofRail.service.app import create_app


class TestOpenApiSnapshot(unittest.TestCase):
    def test_openapi_snapshot_matches(self) -> None:
        client = TestClient(create_app())
        current = client.get("/openapi.json").json()
        current.pop("servers", None)

        snapshot_path = Path(__file__).with_name("openapi.snapshot.json")
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

        self.assertEqual(snapshot, current)
