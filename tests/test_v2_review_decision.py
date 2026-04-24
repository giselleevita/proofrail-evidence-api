import unittest

from fastapi.testclient import TestClient

from ProofRail.service.app import create_app
from ProofRail.service.models import IngestArtifact


class TestV2ReviewDecision(unittest.TestCase):
    def setUp(self) -> None:
        import os

        os.environ["PROOFRAIL_ADMIN_KEY"] = "dev-admin"
        os.environ["PROOFRAIL_SIGNING_SECRET"] = "dev-signing-secret"
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

    def tearDown(self) -> None:
        self.client.close()

    def test_review_decision_stored_and_exported(self) -> None:
        # admin key
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
            json={"screening_type": "onboarding", "subject": {"name": "John Doe"}},
        )
        self.assertEqual(s.status_code, 200)
        screening_id = s.json()["screening_id"]
        evidence_pack_id = s.json()["evidence_pack_id"]

        d = self.client.post(
            f"/v2/screenings/{screening_id}/decision",
            headers={"x-api-key": api_key},
            json={"outcome": "approve", "note": "ok"},
        )
        self.assertEqual(d.status_code, 200)
        self.assertEqual(d.json()["outcome"], "approve")

        exported = self.client.get(
            f"/v2/evidence-packs/{evidence_pack_id}/export?format=json",
            headers={"x-api-key": api_key},
        )
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.json()["review_decision"]["outcome"], "approve")
        self.assertTrue(len(exported.json().get("case_timeline") or []) >= 1)

        case = self.client.get(
            f"/v2/cases/{screening_id}",
            headers={"x-api-key": api_key},
        )
        self.assertEqual(case.status_code, 200)
        body = case.json()
        self.assertEqual(body["case"]["case_id"], screening_id)
        self.assertTrue(len(body["events"]) >= 2)
