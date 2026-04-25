import unittest

from fastapi.testclient import TestClient

from ProofRail.service.app import create_app
from ProofRail.service.models import IngestArtifact


class TestV2Cases(unittest.TestCase):
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

    def test_case_events_endpoint(self) -> None:
        r = self.client.post(
            "/v1/admin/keys",
            headers={"x-admin-key": "dev-admin"},
            json={"customer_id": "c1", "scopes": ["write:screen", "read:evidence", "write:cases"]},
        )
        api_key = r.json()["api_key"]

        s = self.client.post(
            "/v2/screenings",
            headers={"x-api-key": api_key},
            json={"screening_type": "onboarding", "subject": {"name": "John Doe"}},
        )
        case_id = s.json()["screening_id"]

        add = self.client.post(
            f"/v2/cases/{case_id}/events",
            headers={"x-api-key": api_key},
            json={"event_type": "comment", "note": "manual note"},
        )
        self.assertEqual(add.status_code, 200)
        body = add.json()
        self.assertEqual(body["case"]["case_id"], case_id)
        self.assertTrue(any(e["event_type"] == "comment" for e in body["events"]))

        assign = self.client.post(
            f"/v2/cases/{case_id}/events",
            headers={"x-api-key": api_key},
            json={"event_type": "assign", "assignee": "analyst-1"},
        )
        self.assertEqual(assign.status_code, 200)

        q = self.client.get(
            "/v2/cases?assignee=analyst-1",
            headers={"x-api-key": api_key},
        )
        self.assertEqual(q.status_code, 200)
        self.assertTrue(any(item["case_id"] == case_id for item in q.json()))

    def test_list_cases_pagination_header(self) -> None:
        r = self.client.post(
            "/v1/admin/keys",
            headers={"x-admin-key": "dev-admin"},
            json={"customer_id": "c1", "scopes": ["write:screen", "read:evidence", "write:cases"]},
        )
        api_key = r.json()["api_key"]
        for name in ("A", "B", "C"):
            self.client.post(
                "/v2/screenings",
                headers={"x-api-key": api_key},
                json={"screening_type": "onboarding", "subject": {"name": name}},
            )
        resp = self.client.get("/v2/cases?limit=2", headers={"x-api-key": api_key})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 2)
        cursor = resp.headers.get("x-next-cursor")
        self.assertTrue(cursor)
        resp2 = self.client.get(
            f"/v2/cases?limit=2&cursor={cursor}",
            headers={"x-api-key": api_key},
        )
        self.assertEqual(resp2.status_code, 200)
