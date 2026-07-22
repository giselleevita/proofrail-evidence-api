"""A bundle must be verifiable by a third party holding only the public key.

This is the property that makes an evidence bundle meaningful to an auditor or banking
partner. It pins: bundles are signed with Ed25519 when configured, the public key is
fetchable without a credential, verification succeeds with the public key alone, and any
mutation of the signed content is rejected.
"""

from __future__ import annotations

import os
import unittest

from fastapi.testclient import TestClient

from ProofRail.service.app import create_app
from ProofRail.service.models import IngestArtifact
from ProofRail.service.signing import ALG_ED25519, verify_ed25519
from ProofRail.service.storage import canonical_json_bytes


def _fake_ingest(*, store, output_dir, retrieval_ts):  # noqa: ANN001
    blob = store.put_blob(b'{"TEST":["john doe"]}')
    return IngestArtifact(
        retrieval_timestamp=retrieval_ts or "2026-01-01T00:00:00Z",
        list_version="test-list-v1",
        ingestion_run_id="test-run",
        normalized_name_sets_blob_sha256=blob,
        sources=[{"id": "TEST"}],
        entry_counts={"TEST": 1},
        transform_id="test",
        model_version="test",
    )


class TestThirdPartyBundleVerification(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["PROOFRAIL_ADMIN_KEY"] = "dev-admin"
        os.environ["PROOFRAIL_ED25519_KEYS"] = "k1:" + os.urandom(32).hex()
        os.environ["PROOFRAIL_ED25519_KEY_CURRENT"] = "k1"
        os.environ["PROOFRAIL_RPM"] = "500"
        self.client = TestClient(create_app(ingest_func=_fake_ingest))

    def tearDown(self) -> None:
        self.client.close()
        for var in ("PROOFRAIL_ED25519_KEYS", "PROOFRAIL_ED25519_KEY_CURRENT"):
            os.environ.pop(var, None)

    def _bundle(self) -> dict:
        api_key = self.client.post(
            "/v1/admin/keys",
            headers={"x-admin-key": "dev-admin"},
            json={
                "customer_id": "c1",
                "scopes": ["write:screen", "read:evidence", "write:cases"],
            },
        ).json()["api_key"]

        case_id = self.client.post(
            "/v2/screenings",
            headers={"x-api-key": api_key},
            json={"screening_type": "onboarding", "subject": {"name": "Alice Example"}},
        ).json()["screening_id"]

        self.client.post(
            f"/v2/cases/{case_id}/events",
            headers={"x-api-key": api_key},
            json={"event_type": "comment", "note": "reviewed by analyst"},
        )

        r = self.client.get(f"/v2/cases/{case_id}/bundle", headers={"x-api-key": api_key})
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_bundle_is_ed25519_signed_and_carries_public_key(self) -> None:
        doc = self._bundle()
        self.assertEqual(doc["signature"]["algorithm"], ALG_ED25519)
        self.assertTrue(doc["signature"]["public_key"])

    def test_public_keys_endpoint_needs_no_credential(self) -> None:
        doc = self._bundle()
        # No x-api-key header: an external auditor must be able to fetch this.
        r = self.client.get("/v2/signing/public-keys")
        self.assertEqual(r.status_code, 200)
        keys = r.json()["keys"]
        self.assertEqual(keys[0]["key_id"], "k1")
        self.assertEqual(keys[0]["public_key"], doc["signature"]["public_key"])

    def test_verifies_with_public_key_alone(self) -> None:
        doc = self._bundle()
        payload = canonical_json_bytes(doc["bundle"])
        self.assertTrue(
            verify_ed25519(
                doc["signature"]["public_key"], payload, doc["signature"]["signature"]
            )
        )

    def test_tampered_bundle_is_rejected(self) -> None:
        doc = self._bundle()
        doc["bundle"]["case"]["status"] = "tampered"
        payload = canonical_json_bytes(doc["bundle"])
        self.assertFalse(
            verify_ed25519(
                doc["signature"]["public_key"], payload, doc["signature"]["signature"]
            )
        )


if __name__ == "__main__":
    unittest.main()
