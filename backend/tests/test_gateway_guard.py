from fastapi.testclient import TestClient

from app import main
from app.main import app

client = TestClient(app)


def test_local_request_needs_no_gateway_key():
    assert client.get("/api/health").status_code == 200


def test_tunnelled_request_without_key_is_refused(monkeypatch):
    monkeypatch.setattr(main.settings, "gateway_key", "secret-key-for-test")
    response = client.get("/api/health", headers={"cf-connecting-ip": "203.0.113.7"})
    assert response.status_code == 403


def test_tunnelled_request_with_wrong_key_is_refused(monkeypatch):
    monkeypatch.setattr(main.settings, "gateway_key", "secret-key-for-test")
    response = client.get("/api/health", headers={"cf-connecting-ip": "203.0.113.7",
                                                  "x-gateway-key": "nope"})
    assert response.status_code == 403


def test_tunnelled_request_with_key_passes(monkeypatch):
    monkeypatch.setattr(main.settings, "gateway_key", "secret-key-for-test")
    response = client.get("/api/health", headers={"cf-connecting-ip": "203.0.113.7",
                                                  "x-gateway-key": "secret-key-for-test"})
    assert response.status_code == 200


def test_tunnelled_request_is_refused_when_no_key_configured(monkeypatch):
    monkeypatch.setattr(main.settings, "gateway_key", "")
    response = client.get("/api/health", headers={"cf-connecting-ip": "203.0.113.7"})
    assert response.status_code == 403
