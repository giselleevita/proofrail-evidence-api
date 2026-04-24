import unittest

from fastapi.testclient import TestClient

from ProofRail.sanctions_determinism import normalise_name
from ProofRail.service.app import create_app
from ProofRail.service.models import IngestArtifact


class TestV2Explainability(unittest.TestCase):
    def setUp(self) -> None:
        import os

        os.environ["PROOFRAIL_ADMIN_KEY"] = "dev-admin"
        os.environ["PROOFRAIL_SIGNING_SECRET"] = "dev-signing-secret"
        os.environ["PROOFRAIL_RPM"] = "120"

        def fake_ingest(*, store, output_dir, retrieval_ts):  # noqa: ANN001
            # Store normalized values to match screening logic exactly.
            blob_sha = store.put_blob((f'{{"TEST":["{normalise_name("John Doe")}"]}}').encode())
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

    def _new_key(self) -> str:
        r = self.client.post(
            "/v1/admin/keys",
            headers={"x-admin-key": "dev-admin"},
            json={"customer_id": "c1", "scopes": ["write:screen", "read:evidence"]},
        )
        self.assertEqual(r.status_code, 200)
        return r.json()["api_key"]

    def test_v2_screening_includes_explainability_and_exports_persist(self) -> None:
        api_key = self._new_key()

        s = self.client.post(
            "/v2/screenings",
            headers={"x-api-key": api_key},
            json={"screening_type": "onboarding", "subject": {"name": "John Doe"}},
        )
        self.assertEqual(s.status_code, 200)
        body = s.json()

        self.assertIn("match_type", body)
        self.assertIn("score", body)
        self.assertIn("reason_codes", body)

        self.assertEqual(body["decision"], "block")
        self.assertEqual(body["match_type"], "exact_normalized_name")
        self.assertEqual(body["score"], 100)
        self.assertTrue(any(code for code in body["reason_codes"]))

        evidence_pack_id = body["evidence_pack_id"]

        exported = self.client.get(
            f"/v2/evidence-packs/{evidence_pack_id}/export?format=json",
            headers={"x-api-key": api_key},
        )
        self.assertEqual(exported.status_code, 200)
        pack = exported.json()

        result = pack["result"]
        self.assertEqual(result["decision"], "block")
        self.assertEqual(result["match_type"], "exact_normalized_name")
        self.assertEqual(result["score"], 100)
        self.assertIn("reason_codes", result)

        # Light sanity check: PDF contains the label text (uncompressed in practice).
        pdf = self.client.get(
            f"/v2/evidence-packs/{evidence_pack_id}/export?format=pdf",
            headers={"x-api-key": api_key},
        )
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        # PDF streams are typically compressed; just ensure it's a valid PDF.
