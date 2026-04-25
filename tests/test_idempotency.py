import unittest

from fastapi.testclient import TestClient

from ProofRail.service.app import create_app
from ProofRail.service.models import IngestArtifact


class TestIdempotency(unittest.TestCase):
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
        r = self.client.post(
            "/v1/admin/keys",
            headers={"x-admin-key": "dev-admin"},
            json={"customer_id": "c1", "scopes": ["write:screen", "read:evidence", "write:cases"]},
        )
        self.api_key = r.json()["api_key"]

    def tearDown(self) -> None:
        self.client.close()

    def test_screenings_idempotency_key_replays_same_response(self) -> None:
        headers = {"x-api-key": self.api_key, "Idempotency-Key": "k1"}
        body = {"screening_type": "onboarding", "subject": {"name": "Alice Example"}}
        r1 = self.client.post("/v2/screenings", headers=headers, json=body)
        self.assertEqual(r1.status_code, 200)
        r2 = self.client.post("/v2/screenings", headers=headers, json=body)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.json(), r2.json())

        r3 = self.client.post(
            "/v2/screenings",
            headers={"x-api-key": self.api_key, "Idempotency-Key": "k1"},
            json={"screening_type": "onboarding", "subject": {"name": "Different"}},
        )
        self.assertEqual(r3.status_code, 409)
        self.assertEqual(r3.json()["error"]["code"], "idempotency_key_conflict")

    def test_case_events_idempotency(self) -> None:
        s = self.client.post(
            "/v2/screenings",
            headers={"x-api-key": self.api_key},
            json={"screening_type": "onboarding", "subject": {"name": "John Doe"}},
        )
        case_id = s.json()["screening_id"]
        headers = {"x-api-key": self.api_key, "Idempotency-Key": "ev1"}
        r1 = self.client.post(
            f"/v2/cases/{case_id}/events",
            headers=headers,
            json={"event_type": "comment", "note": "hello"},
        )
        self.assertEqual(r1.status_code, 200)
        r2 = self.client.post(
            f"/v2/cases/{case_id}/events",
            headers=headers,
            json={"event_type": "comment", "note": "hello"},
        )
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.json(), r2.json())

    def test_webhook_subscriptions_idempotency(self) -> None:
        headers = {"x-api-key": self.api_key, "Idempotency-Key": "sub1"}
        body = {"url": "https://example.com/webhooks", "secret": "supersecret123", "events": ["*"]}
        r1 = self.client.post("/v2/webhooks/subscriptions", headers=headers, json=body)
        self.assertEqual(r1.status_code, 200)
        r2 = self.client.post("/v2/webhooks/subscriptions", headers=headers, json=body)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.json(), r2.json())
