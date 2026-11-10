"""Sign e2e clients in with the seeded demo viewer (all API reads need a session)."""

from __future__ import annotations

import os

import httpx
import pytest

API = os.environ.get("INCIDENTOPS_API_URL", "http://127.0.0.1:8000")


def sign_in_viewer(client: httpx.Client) -> None:
    password = os.environ.get("INCIDENTOPS_DEMO_VIEWER_PASSWORD", "")
    if not password:
        pytest.fail("INCIDENTOPS_DEMO_VIEWER_PASSWORD is required (run `make seed-users`)")
    response = client.post(
        f"{API}/api/v1/auth/login", json={"username": "viewer", "password": password}
    )
    response.raise_for_status()
    client.cookies.clear()
    client.headers["Authorization"] = f"Session {response.headers['X-Session-Token']}"
