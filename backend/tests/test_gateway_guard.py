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


# --- login through the tunnel: demo account only -------------------------------------------

TUNNEL = {"cf-connecting-ip": "203.0.113.7", "x-gateway-key": "secret-key-for-test"}


def test_tunnelled_login_refuses_any_account_but_demo(client, monkeypatch):
    monkeypatch.setattr(main.settings, "gateway_key", "secret-key-for-test")
    response = client.post("/api/auth/login", headers=TUNNEL,
                           data={"username": "admin", "password": "Admin123!"})
    assert response.status_code == 403


def test_tunnelled_login_refuses_duplicate_username_trick(client, monkeypatch):
    """Gateway read the first 'username' (demo), the backend form parser reads the last (admin)."""
    monkeypatch.setattr(main.settings, "gateway_key", "secret-key-for-test")
    response = client.post("/api/auth/login", headers={**TUNNEL, "content-type": "application/x-www-form-urlencoded"},
                           content="username=demo&username=admin&password=Admin123!")
    assert response.status_code == 403


def test_local_login_is_unrestricted(client):
    response = client.post("/api/auth/login", data={"username": "admin", "password": "Admin123!"})
    assert response.status_code == 200
