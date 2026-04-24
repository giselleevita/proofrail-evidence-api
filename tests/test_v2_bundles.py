import unittest

from fastapi.testclient import TestClient

from ProofRail.service.app import create_app
from ProofRail.service.models import IngestArtifact


class TestV2Bundles(unittest.TestCase):
    def setUp(self) -> None:
        import os

        os.environ["PROOFRAIL_ADMIN_KEY"] = "dev-admin"
        os.environ["PROOFRAIL_SIGNING_SECRET"] = "dev-signing-secret"
        os.environ["PROOFRAIL_SIGNING_KEYS"] = "k1:dev-signing-secret"
        os.environ["PROOFRAIL_SIGNING_KEY_CURRENT"] = "k1"
        os.environ["PROOFRAIL_RPM"] = "120"

        def fake_ingest(*, store, output_dir, retrieval_ts):  # noqa: ANN001
            blob_sha = store.put_blob(b'{"TEST":["john doe"]}')
            return IngestArtifact(
                retrieval_timestamp=retrieval_ts or "2026-01-01T00:00:00Z",
                list_version="test-list-v1",
                ingestion_run_id="test-run",
                normalized_name_sets_blob_sha256=blob_sha,
                sources=[{"id": "TEST"}],
                entry_counts={"TEST": 1},
                transform_id="test",
                model_version="test",
            )

        self.client = TestClient(create_app(ingest_func=fake_ingest))

    def test_case_bundle_signed_and_verifiable(self) -> None:
        r = self.client.post(
            "/v1/admin/keys",
            headers={"x-admin-key": "dev-admin"},
            json={"customer_id": "c1", "scopes": ["write:screen", "read:evidence"]},
        )
        self.assertEqual(r.status_code, 200)
        api_key = r.json()["api_key"]

        s = self.client.post(
            "/v2/screenings",
            headers={"x-api-key": api_key},
            json={"screening_type": "onboarding", "subject": {"name": "Alice Example"}},
        )
        self.assertEqual(s.status_code, 200)
        case_id = s.json()["screening_id"]

        add = self.client.post(
            f"/v2/cases/{case_id}/events",
            headers={"x-api-key": api_key},
            json={"event_type": "comment", "note": "hello"},
        )
        self.assertEqual(add.status_code, 200)

        b = self.client.get(
            f"/v2/cases/{case_id}/bundle",
            headers={"x-api-key": api_key},
        )
        self.assertEqual(b.status_code, 200)
        body = b.json()
        self.assertEqual(body["signature"]["key_id"], "k1")
        self.assertTrue(body["signature"]["signature"])
        self.assertTrue(body["bundle"]["chain_head"])
        self.assertTrue(len(body["bundle"]["events"]) >= 1)

        v = self.client.post(
            "/v2/cases/bundles/verify",
            headers={"x-api-key": api_key},
            json={
                "bundle": body["bundle"],
                "key_id": body["signature"]["key_id"],
                "signature": body["signature"]["signature"],
            },
        )
        self.assertEqual(v.status_code, 200)
        self.assertTrue(v.json()["valid"])
