"""Tests for the API liveness endpoint."""

from fastapi.testclient import TestClient

from incidentops.api.app import create_app
from incidentops.config import Settings


def test_healthz_reports_ok() -> None:
    client = TestClient(create_app(Settings(_env_file=None)))
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["profile"] == "dev"
