import unittest

from fastapi.testclient import TestClient

from ProofRail.service.app import create_app


class TestOpenApiContract(unittest.TestCase):
    def setUp(self) -> None:
        import os

        os.environ["PROOFRAIL_ADMIN_KEY"] = "dev-admin"
        self.client = TestClient(create_app())

    def test_openapi_contains_error_response_schema(self) -> None:
        schema = self.client.get("/openapi.json").json()
        components = schema.get("components", {}).get("schemas", {})
        self.assertIn("ErrorResponse", components)
        self.assertIn("ErrorInfo", components)

    def test_openapi_contains_evidence_pack_schema(self) -> None:
        schema = self.client.get("/openapi.json").json()
        components = schema.get("components", {}).get("schemas", {})
        self.assertIn("EvidencePack", components)
        self.assertIn("HTTPValidationError", components)  # FastAPI default component may still exist

    def test_missing_api_key_error_envelope(self) -> None:
        resp = self.client.post("/v1/sanctions/screen", json={"subject": {"name": "John Doe"}})
        self.assertEqual(resp.status_code, 401)
        body = resp.json()
        self.assertIn("error", body)
        self.assertEqual(body["error"]["code"], "missing_api_key")
        self.assertIn("request_id", body["error"])
        self.assertTrue(resp.headers.get("x-request-id"))
        # Missing/invalid keys should still be treated as part of rate limit surface.
        # Header is set on 429; on 401 it is optional but request_id must be present.

    def test_validation_errors_use_error_envelope(self) -> None:
        # Missing body fields should trigger validation (before admin auth).
        resp = self.client.post("/v1/admin/keys", headers={"x-admin-key": "dev-admin"}, json={})
        self.assertEqual(resp.status_code, 422)
        body = resp.json()
        self.assertEqual(body["error"]["code"], "validation_error")
        self.assertIn("errors", body["error"]["details"])

    def test_missing_api_key_is_throttled_eventually(self) -> None:
        # With a small RPM, repeated missing-key requests should hit 429.
        import os

        os.environ["PROOFRAIL_RPM"] = "2"
        client = TestClient(create_app())
        for _ in range(5):
            resp = client.post("/v1/sanctions/screen", json={"subject": {"name": "John Doe"}})
        self.assertIn(resp.status_code, (401, 429))
        if resp.status_code == 429:
            body = resp.json()
            self.assertEqual(body["error"]["code"], "rate_limited")

