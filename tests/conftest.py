"""Shared pytest fixtures for ProofRail Evidence API test suite."""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("PROOFRAIL_ADMIN_KEY", "test-admin")
os.environ.setdefault("PROOFRAIL_SIGNING_SECRET", "test-signing-secret")
os.environ.setdefault("PROOFRAIL_SIGNING_KEYS", "k1:test-signing-secret")
os.environ.setdefault("PROOFRAIL_SIGNING_KEY_CURRENT", "k1")
os.environ.setdefault("PROOFRAIL_DEMO_MODE", "1")


@pytest.fixture(scope="function")
def tmp_db(tmp_path):
    """Provide a temporary SQLite DB path, cleaned up after each test."""
    db_file = tmp_path / "proofrail_test.db"
    os.environ["PROOFRAIL_DB_PATH"] = str(db_file)
    store_dir = tmp_path / "proofrail_store"
    store_dir.mkdir()
    os.environ["PROOFRAIL_STORE_DIR"] = str(store_dir)
    yield str(db_file)
    os.environ.pop("PROOFRAIL_DB_PATH", None)
    os.environ.pop("PROOFRAIL_STORE_DIR", None)


@pytest.fixture(scope="function")
def client(tmp_db):
    """FastAPI TestClient with a fresh in-memory SQLite DB per test."""
    from ProofRail.service.app import app

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture(scope="function")
def admin_headers():
    """Headers for admin endpoints."""
    return {"x-admin-key": os.environ["PROOFRAIL_ADMIN_KEY"]}


@pytest.fixture(scope="function")
def api_key(client, admin_headers):
    """Create a fresh API key and return it as a string."""
    resp = client.post(
        "/v1/admin/keys",
        headers=admin_headers,
        json={"customer_id": "test-customer", "scopes": ["write:screen", "read:evidence", "write:cases", "read:cases"]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["key"]


@pytest.fixture(scope="function")
def api_headers(api_key):
    """Headers with a valid API key."""
    return {"x-api-key": api_key}
