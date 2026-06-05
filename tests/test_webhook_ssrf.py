import socket

from fastapi.testclient import TestClient

from ProofRail.service.app import create_app
from ProofRail.service.webhooks import deliver_once, validate_webhook_url


def _test_client(monkeypatch, tmp_path):
    monkeypatch.setenv("PROOFRAIL_ADMIN_KEY", "dev-admin")
    monkeypatch.setenv("PROOFRAIL_SIGNING_SECRET", "dev-signing-secret")
    monkeypatch.setenv("PROOFRAIL_DB_PATH", str(tmp_path / "proofrail.db"))
    monkeypatch.setenv("PROOFRAIL_STORE_DIR", str(tmp_path / "store"))

    client = TestClient(create_app())
    response = client.post(
        "/v1/admin/keys",
        headers={"x-admin-key": "dev-admin"},
        json={"customer_id": "c1", "scopes": ["write:screen", "read:evidence"]},
    )
    assert response.status_code == 200, response.text
    return client, response.json()["api_key"]


def test_webhook_subscription_rejects_non_https(monkeypatch, tmp_path) -> None:
    client, api_key = _test_client(monkeypatch, tmp_path)
    response = client.post(
        "/v2/webhooks/subscriptions",
        headers={"x-api-key": api_key},
        json={
            "url": "http://example.com/webhooks",
            "secret": "supersecret123",
            "events": ["screening.created"],
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "webhook_url_not_allowed"


def test_webhook_subscription_rejects_loopback_hostname(monkeypatch, tmp_path) -> None:
    client, api_key = _test_client(monkeypatch, tmp_path)
    response = client.post(
        "/v2/webhooks/subscriptions",
        headers={"x-api-key": api_key},
        json={
            "url": "https://localhost/webhooks",
            "secret": "supersecret123",
            "events": ["screening.created"],
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "webhook_url_not_allowed"


def test_webhook_subscription_rejects_private_ip(monkeypatch, tmp_path) -> None:
    client, api_key = _test_client(monkeypatch, tmp_path)
    response = client.post(
        "/v2/webhooks/subscriptions",
        headers={"x-api-key": api_key},
        json={
            "url": "https://10.0.0.5/webhooks",
            "secret": "supersecret123",
            "events": ["screening.created"],
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "webhook_url_not_allowed"


def test_webhook_subscription_normalizes_public_url(monkeypatch, tmp_path) -> None:
    client, api_key = _test_client(monkeypatch, tmp_path)
    response = client.post(
        "/v2/webhooks/subscriptions",
        headers={"x-api-key": api_key},
        json={
            "url": "https://EXAMPLE.com",
            "secret": "supersecret123",
            "events": ["screening.created"],
        },
    )

    assert response.status_code == 200
    assert response.json()["url"] == "https://example.com/"


def test_validate_webhook_url_preserves_public_ipv6_literal() -> None:
    assert (
        validate_webhook_url("https://[2001:4860:4860::8888]/webhooks")
        == "https://[2001:4860:4860::8888]/webhooks"
    )


def test_deliver_once_rejects_private_dns_resolution(monkeypatch) -> None:
    def fake_getaddrinfo(host, port, type):  # noqa: A002, ANN001
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr("ProofRail.service.webhooks.socket.getaddrinfo", fake_getaddrinfo)

    class BlockedClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            raise AssertionError("HTTP client should not be opened for blocked webhook URL")

    monkeypatch.setattr("ProofRail.service.webhooks.httpx.Client", BlockedClient)

    result = deliver_once(
        url="https://hooks.example.test/webhook",
        secret="supersecret123",
        event_type="screening.created",
        event_id="evt-1",
        payload_json='{"ok":true}',
        timeout_s=1.0,
    )

    assert result.ok is False
    assert result.error == "webhook_url_not_allowed"


def test_deliver_once_posts_to_normalized_public_url(monkeypatch) -> None:
    def fake_getaddrinfo(host, port, type):  # noqa: A002, ANN001
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    seen = {}

    class FakeResponse:
        status_code = 204

    class FakeClient:
        def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
            return None

        def post(self, url, content, headers):  # noqa: ANN001
            seen["url"] = url
            seen["content"] = content
            seen["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr("ProofRail.service.webhooks.socket.getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr("ProofRail.service.webhooks.httpx.Client", FakeClient)

    result = deliver_once(
        url="https://EXAMPLE.com/webhook",
        secret="supersecret123",
        event_type="screening.created",
        event_id="evt-1",
        payload_json='{"ok":true}',
        timeout_s=1.0,
    )

    assert result.ok is True
    assert seen["url"] == "https://example.com/webhook"
    assert seen["headers"]["x-proofrail-signature"].startswith("sha256=")
