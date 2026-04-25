import os
import unittest

from fastapi.testclient import TestClient

from ProofRail.service.app import create_app


class TestPrometheusMetrics(unittest.TestCase):
    def setUp(self) -> None:
        for k in ("PROOFRAIL_PROMETHEUS_METRICS", "PROOFRAIL_METRICS_BEARER_TOKEN"):
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k in ("PROOFRAIL_PROMETHEUS_METRICS", "PROOFRAIL_METRICS_BEARER_TOKEN"):
            os.environ.pop(k, None)

    def test_metrics_available_when_enabled(self) -> None:
        os.environ["PROOFRAIL_PROMETHEUS_METRICS"] = "1"
        client = TestClient(create_app())
        r = client.get("/metrics")
        self.assertEqual(r.status_code, 200)
        self.assertIn("proofrail_", r.text)

    def test_metrics_bearer_required_when_configured(self) -> None:
        os.environ["PROOFRAIL_PROMETHEUS_METRICS"] = "1"
        os.environ["PROOFRAIL_METRICS_BEARER_TOKEN"] = "metrics-test-token"
        client = TestClient(create_app())
        self.assertEqual(client.get("/metrics").status_code, 401)
        r = client.get("/metrics", headers={"Authorization": "Bearer metrics-test-token"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("proofrail_", r.text)
