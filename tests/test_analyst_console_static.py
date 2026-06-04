import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from ProofRail.service.app import create_app


class TestAnalystConsoleStatic(unittest.TestCase):
    def setUp(self) -> None:
        import os

        os.environ["PROOFRAIL_ADMIN_KEY"] = "dev-admin"
        os.environ["PROOFRAIL_SIGNING_SECRET"] = "dev-signing-secret"
        os.environ["PROOFRAIL_RPM"] = "120"
        os.environ["PROOFRAIL_ENABLE_CONSOLE"] = "1"

        self.client = TestClient(create_app())

    def tearDown(self) -> None:
        self.client.close()

    def test_console_index_returns_200(self) -> None:
        r = self.client.get("/console/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"ProofRail analyst console", r.content)

    def test_console_index_html_alias(self) -> None:
        r = self.client.get("/console/index.html")
        self.assertEqual(r.status_code, 200)

    def test_console_can_be_disabled_for_production(self) -> None:
        with patch.dict("os.environ", {"PROOFRAIL_ENABLE_CONSOLE": "0"}):
            client = TestClient(create_app())
            try:
                r = client.get("/console/")
                self.assertEqual(r.status_code, 404)
            finally:
                client.close()
