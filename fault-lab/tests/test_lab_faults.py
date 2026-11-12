"""Tests for the benchmark-only fault controller."""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from conftest import TOKENS
from fastapi.testclient import TestClient
from fault_lab.auth import LabAuthenticator, LabRole
from fault_lab.faults.app import DbLockFixture, create_app


class FakeHolder:
    """Records lock acquisition and release without a database."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.acquired = 0
        self.released = 0

    async def __call__(self, release: asyncio.Event, hold_seconds: float) -> None:
        if self.fail:
            raise ConnectionError("lab db unreachable")
        self.acquired += 1
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(release.wait(), timeout=hold_seconds)
        self.released += 1


def _auth(role: LabRole) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[role]}"}


@pytest.fixture
def holder() -> FakeHolder:
    return FakeHolder()


@pytest.fixture
def client(holder: FakeHolder, authenticator: LabAuthenticator) -> TestClient:
    return TestClient(create_app(DbLockFixture(holder), authenticator))


@pytest.mark.parametrize("role", [LabRole.EXECUTOR, LabRole.READER])
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("get", "/faults", None),
        ("post", "/faults/db-lock", {"action_id": "f1", "hold_seconds": 5}),
        ("post", "/faults/db-lock/release", {"fixture_token": "f1"}),
        ("post", "/faults/reset", None),
    ],
)
def test_non_benchmark_roles_are_forbidden(
    client: TestClient, role: LabRole, method: str, path: str, body: dict[str, object] | None
) -> None:
    # The agent (reader) and executor can neither inject nor observe ground truth.
    response = client.request(method, path, json=body, headers=_auth(role))
    assert response.status_code == 403


def test_unauthenticated_is_rejected(client: TestClient) -> None:
    assert client.get("/faults").status_code == 401


def test_inject_release_cycle(client: TestClient, holder: FakeHolder) -> None:
    with client:
        injected = client.post(
            "/faults/db-lock",
            json={"action_id": "fix-1", "hold_seconds": 30},
            headers=_auth(LabRole.BENCHMARK),
        )
        assert injected.status_code == 201
        assert injected.json()["db_lock"]["fixture_token"] == "fix-1"

        duplicate = client.post(
            "/faults/db-lock",
            json={"action_id": "fix-2", "hold_seconds": 30},
            headers=_auth(LabRole.BENCHMARK),
        )
        assert duplicate.status_code == 409

        wrong = client.post(
            "/faults/db-lock/release",
            json={"fixture_token": "fix-9"},
            headers=_auth(LabRole.BENCHMARK),
        )
        assert wrong.status_code == 404

        released = client.post(
            "/faults/db-lock/release",
            json={"fixture_token": "fix-1"},
            headers=_auth(LabRole.BENCHMARK),
        )
        assert released.json() == {"db_lock": None}
    assert (holder.acquired, holder.released) == (1, 1)


def test_hold_is_bounded(client: TestClient) -> None:
    response = client.post(
        "/faults/db-lock",
        json={"action_id": "fix-1", "hold_seconds": 601},
        headers=_auth(LabRole.BENCHMARK),
    )
    assert response.status_code == 422


def test_reset_releases_active_fixture(client: TestClient, holder: FakeHolder) -> None:
    with client:
        client.post(
            "/faults/db-lock",
            json={"action_id": "fix-1", "hold_seconds": 30},
            headers=_auth(LabRole.BENCHMARK),
        )
        assert client.post("/faults/reset", headers=_auth(LabRole.BENCHMARK)).json() == {
            "db_lock": None
        }
    assert holder.released == 1


async def test_failed_acquisition_is_reported() -> None:
    fixture = DbLockFixture(FakeHolder(fail=True))
    with pytest.raises(RuntimeError, match="failed to start the db_lock fixture"):
        await fixture.inject("fix-1", 5)
    assert fixture.status() == {"db_lock": None}


async def test_hold_expires_on_its_own() -> None:
    holder = FakeHolder()
    fixture = DbLockFixture(holder)
    await fixture.inject("fix-1", 0.3)
    await asyncio.sleep(0.5)
    assert fixture.status() == {"db_lock": None}
    assert holder.released == 1
