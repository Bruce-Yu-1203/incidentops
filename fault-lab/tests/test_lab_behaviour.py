"""Injectable API behaviours and the internal control endpoint."""

from __future__ import annotations

import sys
import time

import pytest
from fastapi.testclient import TestClient
from fault_lab.api.app import create_app
from fault_lab.api.behaviour import LEAK_CHUNK_BYTES, Behaviour, InjectedFailureError, Profile
from test_lab_services import FakeDeps

INTERNAL = {"Authorization": "Bearer internal-token-0123456789"}


def _client(behaviour: Behaviour, deps: FakeDeps | None = None) -> TestClient:
    return TestClient(
        create_app(
            deps or FakeDeps(), behaviour=behaviour, internal_token="internal-token-0123456789"
        )
    )


def test_faulty_release_fails_a_share_of_requests() -> None:
    behaviour = Behaviour()
    behaviour.deploy("rel-2", Profile.FAULTY)
    failures = 0
    for _ in range(400):
        try:
            behaviour.on_request()
        except InjectedFailureError:
            failures += 1
    assert 80 < failures < 160  # about 30 %


def test_leaky_release_retains_memory_until_rollback() -> None:
    behaviour = Behaviour()
    behaviour.deploy("rel-2", Profile.LEAKY)
    for _ in range(10):
        behaviour.on_request()
    assert behaviour.leaked_bytes == 10 * LEAK_CHUNK_BYTES
    behaviour.deploy("rel-1", Profile.HEALTHY)
    assert behaviour.leaked_bytes == 0


def test_cpu_load_starts_and_stops() -> None:
    behaviour = Behaviour()
    behaviour.start_cpu_load(threads=1)
    assert behaviour.cpu_load_active
    behaviour.stop_cpu_load()
    assert not behaviour.cpu_load_active


def test_noise_is_time_bounded() -> None:
    behaviour = Behaviour()
    behaviour.start_noise(0.05)
    assert behaviour.snapshot()["noise_active"] is True
    time.sleep(0.1)
    for _ in range(50):
        behaviour.on_request()  # never raises once the noise window ended


def test_internal_endpoint_requires_the_internal_token() -> None:
    client = _client(Behaviour())
    assert client.post("/internal/behaviour", json={"reset": True}).status_code == 403
    wrong = {"Authorization": "Bearer nope"}
    assert (
        client.post("/internal/behaviour", json={"reset": True}, headers=wrong).status_code == 403
    )


def test_deploy_through_endpoint_updates_release_metric() -> None:
    behaviour = Behaviour()
    client = _client(behaviour)
    body = {"release_id": "rel-2", "profile": "faulty"}
    assert (
        client.post("/internal/behaviour", json=body, headers=INTERNAL).json()["release_id"]
        == "rel-2"
    )
    metrics = client.get("/metrics").text
    assert 'lab_api_release_info{release="rel-2"} 1.0' in metrics
    assert "rel-1" not in metrics.split("lab_api_release_info")[-1].split("\n")[0]
    # Fault-control calls must not show up in agent-visible request metrics.
    assert "/internal/behaviour" not in metrics
    if sys.platform.startswith("linux"):  # ProcessCollector reads /proc
        assert "process_resident_memory_bytes" in metrics


@pytest.mark.parametrize(
    "body", [{"release_id": "v2-faulty", "profile": "faulty"}, {"profile": "evil"}]
)
def test_internal_endpoint_rejects_bad_values(body: dict[str, str]) -> None:
    assert (
        _client(Behaviour()).post("/internal/behaviour", json=body, headers=INTERNAL).status_code
        == 422
    )


def test_items_fail_when_cache_is_down_or_release_is_faulty() -> None:
    deps = FakeDeps()
    deps.cache_down = True
    client = _client(Behaviour(), deps)
    assert client.get("/items/1").status_code == 503
    deps.cache_down = False
    behaviour = Behaviour()
    behaviour.deploy("rel-2", Profile.FAULTY)
    client = _client(behaviour, deps)
    codes = {client.get("/items/1").status_code for _ in range(40)}
    assert codes == {200, 500}


def test_error_path_fails_every_fourth_item_with_a_stack_trace(
    caplog: pytest.LogCaptureFixture,
) -> None:
    behaviour = Behaviour()
    client = _client(behaviour)
    assert client.get("/items/4").status_code == 200
    response = client.post("/internal/behaviour", json={"error_path": True}, headers=INTERNAL)
    assert response.json()["error_path"] is True
    # The release does not change: this is a latent bug, not a deploy (B-F10).
    assert response.json()["release_id"] == "rel-1"
    with caplog.at_level("ERROR", logger="fault_lab.api"):
        assert client.get("/items/4").status_code == 500
        assert client.get("/items/5").status_code == 200
    (record,) = [r for r in caplog.records if "unhandled exception" in r.getMessage()]
    message = record.getMessage()
    assert "Traceback (most recent call last)" in message
    assert "KeyError: 'pricing'" in message

    client.post("/internal/behaviour", json={"reset": True}, headers=INTERNAL)
    assert behaviour.error_path is False
    assert client.get("/items/4").status_code == 200


def test_config_push_changes_the_items_database_only_with_safe_values() -> None:
    behaviour = Behaviour()
    client = _client(behaviour)
    assert client.get("/items/1").status_code == 200
    pushed = client.post(
        "/internal/behaviour", json={"config": {"items_database": "lab_replica"}}, headers=INTERNAL
    )
    assert pushed.json()["config"] == {"items_database": "lab_replica"}
    assert pushed.json()["release_id"] == "rel-1"
    # The API now reads a database that does not exist: every item fails.
    assert client.get("/items/1").status_code == 503
    client.post("/internal/behaviour", json={"reset": True}, headers=INTERNAL)
    assert client.get("/items/1").status_code == 200


@pytest.mark.parametrize(
    "config",
    [
        {"items_database": "postgres://evil-host/db"},
        {"items_database": "lab; drop"},
        {"dsn": "lab"},
    ],
)
def test_config_push_rejects_hosts_and_unknown_keys(config: dict[str, str]) -> None:
    response = _client(Behaviour()).post(
        "/internal/behaviour", json={"config": config}, headers=INTERNAL
    )
    assert response.status_code == 422
