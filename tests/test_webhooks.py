import unittest
from unittest import mock

from fastapi.testclient import TestClient

from ProofRail.service.app import create_app
from ProofRail.service.models import IngestArtifact
from ProofRail.service.worker import process_once


class TestWebhooks(unittest.TestCase):
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
            json={"customer_id": "c1", "scopes": ["write:screen", "read:evidence"]},
        )
        self.api_key = r.json()["api_key"]

    def test_subscribe_emits_and_admin_runner_delivers(self) -> None:
        # Create subscription
        sub = self.client.post(
            "/v2/webhooks/subscriptions",
            headers={"x-api-key": self.api_key},
            json={
                "url": "https://example.com/webhooks",
                "secret": "supersecret123",
                "events": ["screening.created"],
            },
        )
        self.assertEqual(sub.status_code, 200)

        # Trigger event: create screening (will enqueue delivery)
        s = self.client.post(
            "/v2/screenings",
            headers={"x-api-key": self.api_key},
            json={"screening_type": "onboarding", "subject": {"name": "Alice Example"}},
        )
        self.assertEqual(s.status_code, 200)

        # Admin runner should enqueue jobs (worker delivers)
        with mock.patch(
            "ProofRail.service.worker.deliver_once",
            return_value=type("R", (), {"ok": True, "status_code": 200, "error": None})(),
        ):
            run = self.client.post(
                "/v1/admin/webhooks/deliveries/run",
                headers={"x-admin-key": "dev-admin"},
                json={},
            )
            self.assertEqual(run.status_code, 200)
            # Process queued jobs once (simulates worker tick)
            process_once(self.client.app.state.proofrail)
        body = run.json()
        self.assertTrue(body["attempted"] >= 1)

        # Delete subscription
        subs = self.client.get(
            "/v2/webhooks/subscriptions",
            headers={"x-api-key": self.api_key},
        ).json()
        sub_id = subs[0]["subscription_id"]
        d = self.client.delete(
            f"/v2/webhooks/subscriptions/{sub_id}",
            headers={"x-api-key": self.api_key},
        )
        self.assertEqual(d.status_code, 200)
