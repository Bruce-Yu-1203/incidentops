"""Authentication behaviour of the API that needs no database."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import SecretStr

from incidentops.api.app import create_app
from incidentops.config import Profile, Settings

TOKEN = "alertmanager-token-0123456789"


def _client(**overrides: object) -> TestClient:
    settings = Settings(_env_file=None, **overrides)  # type: ignore[arg-type]
    return TestClient(create_app(settings))


def test_webhook_fails_closed_without_configured_token() -> None:
    response = _client().post("/api/v1/alerts/alertmanager", json={})
    assert response.status_code == 503


def test_webhook_rejects_missing_or_wrong_token() -> None:
    client = _client(alertmanager_token=SecretStr(TOKEN))
    assert client.post("/api/v1/alerts/alertmanager", json={}).status_code == 401
    wrong = {"Authorization": "Bearer not-the-right-token-000"}
    assert client.post("/api/v1/alerts/alertmanager", json={}, headers=wrong).status_code == 401


def test_webhook_rejects_malformed_payload_after_auth() -> None:
    client = _client(alertmanager_token=SecretStr(TOKEN))
    response = client.post(
        "/api/v1/alerts/alertmanager",
        json={"version": "4", "alerts": []},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 422


def test_incident_reads_require_a_session_in_every_profile() -> None:
    client = _client(profile=Profile.DEV)
    assert client.get("/api/v1/incidents").status_code == 401
    assert client.get("/api/v1/incidents/inc_x").status_code == 401
