import unittest

from fastapi.testclient import TestClient

from ProofRail.service.app import create_app
from ProofRail.service.models import IngestArtifact


class TestPdfExport(unittest.TestCase):
    def setUp(self) -> None:
        import os

        os.environ["PROOFRAIL_ADMIN_KEY"] = "dev-admin"
        os.environ["PROOFRAIL_SIGNING_SECRET"] = "dev-signing-secret"

        def fake_ingest(*, store, output_dir, retrieval_ts):  # noqa: ANN001
            # Keep tests deterministic and offline.
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

    def _create_key(self, customer_id: str, scopes: list[str]) -> str:
        resp = self.client.post(
            "/v1/admin/keys",
            headers={"x-admin-key": "dev-admin"},
            json={"customer_id": customer_id, "scopes": scopes},
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()["api_key"]

    def _screen(self, api_key: str, name: str) -> str:
        resp = self.client.post(
            "/v1/sanctions/screen",
            headers={"x-api-key": api_key},
            json={"subject": {"name": name}},
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()["evidence_pack_id"]

    def test_pdf_export_requires_scope(self) -> None:
        key = self._create_key("c1", ["write:screen"])
        evidence_id = self._screen(key, "John Doe")

        resp = self.client.get(
            f"/v1/evidence-packs/{evidence_id}/export.pdf",
            headers={"x-api-key": key},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["error"]["code"], "missing_scope")

    def test_pdf_export_content_type_and_tenant_isolation(self) -> None:
        key1 = self._create_key("c1", ["write:screen", "read:evidence"])
        key2 = self._create_key("c2", ["write:screen", "read:evidence"])
        evidence_id = self._screen(key1, "John Doe")

        ok = self.client.get(
            f"/v1/evidence-packs/{evidence_id}/export.pdf",
            headers={"x-api-key": key1},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.headers.get("content-type"), "application/pdf")
        self.assertTrue(ok.content.startswith(b"%PDF"))

        other = self.client.get(
            f"/v1/evidence-packs/{evidence_id}/export.pdf",
            headers={"x-api-key": key2},
        )
        self.assertEqual(other.status_code, 404)
        self.assertEqual(other.json()["error"]["code"], "evidence_pack_not_found")
