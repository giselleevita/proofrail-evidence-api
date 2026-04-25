import os
import unittest

from fastapi.testclient import TestClient

from ProofRail.service.app import create_app


class TestPreauthRateLimit(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = os.environ.get("PROOFRAIL_PREAUTH_RPM")
        os.environ["PROOFRAIL_ADMIN_KEY"] = "dev-admin"
        os.environ["PROOFRAIL_SIGNING_SECRET"] = "dev-signing-secret"
        os.environ["PROOFRAIL_PREAUTH_RPM"] = "2"

        def fake_ingest(*, store, output_dir, retrieval_ts):  # noqa: ANN001
            from ProofRail.service.models import IngestArtifact

            blob_sha = store.put_blob(b'{"TEST":["x"]}')
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
        if self._prev is None:
            os.environ.pop("PROOFRAIL_PREAUTH_RPM", None)
        else:
            os.environ["PROOFRAIL_PREAUTH_RPM"] = self._prev

    def test_invalid_api_key_throttled_before_db_hot_loop(self) -> None:
        codes = []
        for _ in range(4):
            r = self.client.get(
                "/v2/cases",
                headers={"x-api-key": "not-a-real-key"},
            )
            codes.append(r.status_code)
        self.assertIn(429, codes)
