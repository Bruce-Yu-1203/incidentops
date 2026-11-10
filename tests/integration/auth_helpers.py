"""Sign a test client in: every API read requires a session (ADR-016 retired)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from incidentops.auth.service import HumanRole, create_user

TEST_PASSWORD = "lab-password-0123"


def sign_in(
    client: TestClient, factory: sessionmaker[Session], role: HumanRole = HumanRole.VIEWER
) -> dict[str, str]:
    """Create a fresh user with ``role`` and attach its session to ``client``."""
    username = f"{role.value}-{uuid.uuid4().hex[:8]}"
    with factory.begin() as session:
        create_user(session, username, TEST_PASSWORD, role)
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": TEST_PASSWORD}
    )
    response.raise_for_status()
    headers = {"Authorization": f"Session {response.headers['X-Session-Token']}"}
    client.cookies.clear()
    client.headers.update(headers)
    return headers
