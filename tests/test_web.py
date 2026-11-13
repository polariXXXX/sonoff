import json

import pytest
from fastapi.testclient import TestClient

from sonoff.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_PASSWORD", "test-password-with-32-characters")
    monkeypatch.setenv("CLOUD_BACKEND", "mock")
    monkeypatch.delenv("SENSOR_BACKEND", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"sockets": [{"device_id": "boiler", "name": "Бойлер"}]}))
    monkeypatch.setenv("CONFIG_PATH", str(config))
    with TestClient(create_app()) as client:
        yield client


def auth():
    return ("admin", "test-password-with-32-characters")


def test_auth_and_csrf_required_for_control(client):
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", auth=("admin", "wrong")).status_code == 401
    assert (
        client.post("/api/sockets/boiler/control", auth=auth(), json={"action": "off"}).status_code
        == 403
    )
    state = client.get("/api/state", auth=auth()).json()
    headers = {"X-Control-Token": state["control_token"]}
    response = client.post(
        "/api/sockets/boiler/control", auth=auth(), headers=headers, json={"action": "off"}
    )
    assert response.status_code == 200


def test_manual_on_blocked_when_sensor_unknown(client):
    state = client.get("/api/state", auth=auth()).json()
    response = client.post(
        "/api/sockets/boiler/control",
        auth=auth(),
        headers={"X-Control-Token": state["control_token"]},
        json={"action": "on"},
    )
    assert response.status_code == 409


def test_home_and_security_headers(client):
    response = client.get("/", auth=auth())
    assert response.status_code == 200
    assert "Енергія під контролем" in response.text
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_configuration_persists_across_restart(client):
    state = client.get("/api/state", auth=auth()).json()
    headers = {"X-Control-Token": state["control_token"]}
    config = {
        "device_id": "boiler",
        "name": "Updated",
        "mains_present": {"action": "on", "delay_minutes": 30},
    }
    assert (
        client.put("/api/sockets/boiler", auth=auth(), headers=headers, json=config).status_code
        == 200
    )
    with TestClient(create_app()) as restarted:
        socket = restarted.get("/api/state", auth=auth()).json()["sockets"][0]
        assert socket["name"] == "Updated"
        assert socket["mains_present"]["delay_minutes"] == 30


def test_invalid_delay_and_mismatched_id_rejected(client):
    headers = {"X-Control-Token": client.get("/api/state", auth=auth()).json()["control_token"]}
    assert (
        client.put(
            "/api/sockets/boiler",
            auth=auth(),
            headers=headers,
            json={"device_id": "different", "name": "x"},
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/api/sockets/boiler",
            auth=auth(),
            headers=headers,
            json={
                "device_id": "boiler",
                "name": "x",
                "mains_present": {"action": "on", "delay_minutes": -1},
            },
        ).status_code
        == 422
    )


def test_short_password_rejected(monkeypatch):
    monkeypatch.setenv("WEB_PASSWORD", "short")
    with pytest.raises(ValueError):
        create_app()
