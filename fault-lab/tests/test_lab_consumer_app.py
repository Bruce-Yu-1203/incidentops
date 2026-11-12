"""HTTP contract tests for the consumer management API."""

from __future__ import annotations

import pytest
from conftest import TOKENS
from fastapi.testclient import TestClient
from fault_lab.auth import LabAuthenticator, LabRole
from fault_lab.consumer.app import Throttle, create_app
from fault_lab.consumer.controller import ConsumerController


@pytest.fixture
def client(controller: ConsumerController, authenticator: LabAuthenticator) -> TestClient:
    return TestClient(create_app(controller, authenticator))


def _auth(role: LabRole) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[role]}"}


def test_status_requires_authentication(client: TestClient) -> None:
    assert client.get("/control/status").status_code == 401
    assert (
        client.get("/control/status", headers={"Authorization": "Bearer nope"}).status_code == 401
    )


@pytest.mark.parametrize("role", list(LabRole))
def test_any_lab_role_can_read_status(client: TestClient, role: LabRole) -> None:
    body = client.get("/control/status", headers=_auth(role)).json()
    assert body == {"consumer_id": "persist-demo-consumer", "paused": False, "generation": "gen-1"}


@pytest.mark.parametrize("role", [LabRole.EXECUTOR, LabRole.READER])
def test_only_benchmark_can_pause(client: TestClient, role: LabRole) -> None:
    response = client.post(
        "/control/pause",
        headers=_auth(role),
        json={"action_id": "p1", "expected_generation": "gen-1"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"


def test_full_pause_resume_flow_and_operation_lookup(client: TestClient) -> None:
    paused = client.post(
        "/control/pause",
        headers=_auth(LabRole.BENCHMARK),
        json={"action_id": "p1", "expected_generation": "gen-1"},
    )
    assert paused.status_code == 201
    assert paused.json()["paused"] is True

    body = {"action_id": "act_42", "desired_paused": False, "expected_generation": "gen-2"}
    resumed = client.post("/control/resume", headers=_auth(LabRole.EXECUTOR), json=body)
    assert resumed.status_code == 201
    assert resumed.json()["generation"] == "gen-3"

    replay = client.post("/control/resume", headers=_auth(LabRole.EXECUTOR), json=body)
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True

    lookup = client.get("/control/operations/act_42", headers=_auth(LabRole.READER))
    assert lookup.status_code == 200
    assert lookup.json()["generation_after"] == "gen-3"


def test_stale_generation_returns_409(client: TestClient) -> None:
    response = client.post(
        "/control/resume",
        headers=_auth(LabRole.EXECUTOR),
        json={"action_id": "a", "desired_paused": False, "expected_generation": "gen-9"},
    )
    assert response.status_code == 409
    assert response.json()["error"] == "stale_generation"


def test_unknown_operation_returns_404(client: TestClient) -> None:
    response = client.get("/control/operations/missing", headers=_auth(LabRole.READER))
    assert response.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"action_id": "a", "desired_paused": False, "expected_generation": "gen-1", "cmd": "x"},
        {"action_id": "a/../b", "desired_paused": False, "expected_generation": "gen-1"},
    ],
)
def test_resume_rejects_unknown_fields_and_bad_ids(
    client: TestClient, body: dict[str, object]
) -> None:
    response = client.post("/control/resume", headers=_auth(LabRole.EXECUTOR), json=body)
    assert response.status_code == 422


def test_reset_by_benchmark(client: TestClient) -> None:
    response = client.post(
        "/control/reset", headers=_auth(LabRole.BENCHMARK), json={"action_id": "r1"}
    )
    assert response.status_code == 201
    assert response.json()["paused"] is False


def test_metrics_reflect_paused_state(client: TestClient) -> None:
    client.post(
        "/control/pause",
        headers=_auth(LabRole.BENCHMARK),
        json={"action_id": "p1", "expected_generation": "gen-1"},
    )
    text = client.get("/metrics").text
    assert "lab_consumer_paused 1.0" in text
    assert "lab_consumer_generation 2.0" in text


@pytest.mark.parametrize("role", [LabRole.EXECUTOR, LabRole.READER])
def test_only_benchmark_can_slow_the_consumer(client: TestClient, role: LabRole) -> None:
    response = client.post(
        "/control/processing-delay", headers=_auth(role), json={"action_id": "s", "delay_ms": 100}
    )
    assert response.status_code == 403


def test_processing_delay_is_hidden_and_cleared_by_reset(
    controller: ConsumerController, authenticator: LabAuthenticator
) -> None:
    throttle = Throttle()
    client = TestClient(create_app(controller, authenticator, throttle=throttle))
    applied = client.post(
        "/control/processing-delay",
        headers=_auth(LabRole.BENCHMARK),
        json={"action_id": "s1", "delay_ms": 100},
    )
    assert applied.status_code == 200
    assert throttle.delay_ms == 100
    # Fault state never appears in agent-visible status or metrics.
    status = client.get("/control/status", headers=_auth(LabRole.READER)).json()
    assert status == {
        "consumer_id": "persist-demo-consumer",
        "paused": False,
        "generation": "gen-1",
    }
    assert "delay" not in client.get("/metrics").text
    client.post("/control/reset", headers=_auth(LabRole.BENCHMARK), json={"action_id": "r1"})
    assert throttle.delay_ms == 0


@pytest.mark.parametrize("delay", [-1, 1001])
def test_processing_delay_is_bounded(client: TestClient, delay: int) -> None:
    response = client.post(
        "/control/processing-delay",
        headers=_auth(LabRole.BENCHMARK),
        json={"action_id": "s", "delay_ms": delay},
    )
    assert response.status_code == 422


async def test_throttle_sleeps_per_record() -> None:
    import time

    throttle = Throttle()
    start = time.perf_counter()
    await throttle.wait(10)
    assert time.perf_counter() - start < 0.01
    throttle.delay_ms = 5
    start = time.perf_counter()
    await throttle.wait(4)
    assert time.perf_counter() - start >= 0.018
