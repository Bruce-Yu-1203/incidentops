"""Fault controller: injections, target revisions and allowlisted remediations."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import pytest
from conftest import TOKENS
from fastapi.testclient import TestClient
from fault_lab.auth import LabAuthenticator, LabRole
from fault_lab.faults.app import DbLockFixture, create_app
from fault_lab.faults.controller import HEALTHY_RELEASE, LabController, Ledger, _digest


class FakeLabApi:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def update(self, **fields: Any) -> dict[str, Any]:
        self.calls.append(fields)
        return fields


class FakeRedis:
    def __init__(self) -> None:
        self.paused = False

    async def pause(self, milliseconds: int) -> None:
        self.paused = True

    async def unpause(self) -> None:
        self.paused = False


class FakeOutage:
    def __init__(self) -> None:
        self.active = False
        self.seconds: float | None = None

    async def start(self, seconds: float) -> None:
        self.active, self.seconds = True, seconds

    async def stop(self) -> None:
        self.active = False


class Holder:
    async def __call__(self, release: asyncio.Event, hold_seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(release.wait(), timeout=hold_seconds)


@pytest.fixture
def outages(
    tmp_path: Path, authenticator: LabAuthenticator
) -> tuple[TestClient, FakeOutage, FakeOutage]:
    kafka, postgres = FakeOutage(), FakeOutage()
    fixture = DbLockFixture(Holder())
    controller = LabController(
        fixture,
        FakeLabApi(),
        FakeRedis(),
        Ledger(tmp_path / "outages.sqlite3"),
        kafka=kafka,
        postgres=postgres,
    )
    return TestClient(create_app(fixture, authenticator, controller)), kafka, postgres


@pytest.fixture
def parts(
    tmp_path: Path, authenticator: LabAuthenticator
) -> tuple[TestClient, FakeLabApi, FakeRedis]:
    lab_api, redis = FakeLabApi(), FakeRedis()
    fixture = DbLockFixture(Holder())
    controller = LabController(fixture, lab_api, redis, Ledger(tmp_path / "ledger.sqlite3"))
    return TestClient(create_app(fixture, authenticator, controller)), lab_api, redis


def _auth(role: LabRole) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[role]}"}


def _remediate(client: TestClient, role: LabRole = LabRole.EXECUTOR, **body: Any) -> Any:
    return client.post("/remediations", json=body, headers=_auth(role))


def test_rollback_requires_pinned_release_and_current_revision(
    parts: tuple[TestClient, FakeLabApi, FakeRedis],
) -> None:
    client, lab_api, _ = parts
    with client:
        deployed = client.post(
            "/faults/deploy",
            json={"action_id": "d1", "profile": "faulty"},
            headers=_auth(LabRole.BENCHMARK),
        ).json()
        assert deployed["current_release"] == "rel-2"
        # The timeline is visible to readers but never names the injected profile.
        timeline = client.get("/deployments/lab-api", headers=_auth(LabRole.READER)).json()
        assert "faulty" not in str(timeline)
        assert (
            client.get("/targets/lab-api", headers=_auth(LabRole.READER)).json()["revision"]
            == "rel-2"
        )

        arbitrary = _remediate(
            client,
            action_id="a1",
            action_type="rollback_demo_api",
            target_id="lab-api",
            expected_revision="rel-2",
            arguments={"target_release": "rel-9"},
        )
        assert arbitrary.status_code == 400
        stale = _remediate(
            client,
            action_id="a2",
            action_type="rollback_demo_api",
            target_id="lab-api",
            expected_revision="rel-1",
            arguments={"target_release": HEALTHY_RELEASE},
        )
        assert stale.json()["error"] == "stale_revision"
        ok = _remediate(
            client,
            action_id="a3",
            action_type="rollback_demo_api",
            target_id="lab-api",
            expected_revision="rel-2",
            arguments={"target_release": HEALTHY_RELEASE},
        )
        assert ok.status_code == 201
        assert ok.json()["revision_after"] == HEALTHY_RELEASE
        again = _remediate(
            client,
            action_id="a3",
            action_type="rollback_demo_api",
            target_id="lab-api",
            expected_revision="rel-2",
            arguments={"target_release": HEALTHY_RELEASE},
        )
        assert again.status_code == 200  # replayed receipt, no second deploy
    assert lab_api.calls[-1] == {"release_id": HEALTHY_RELEASE, "profile": "healthy"}
    assert sum(1 for c in lab_api.calls if c.get("release_id") == HEALTHY_RELEASE) == 1


def test_db_blocker_release_is_bound_to_the_fixture_token(
    parts: tuple[TestClient, FakeLabApi, FakeRedis],
) -> None:
    client, _, _ = parts
    with client:
        client.post(
            "/faults/db-lock",
            json={"action_id": "lock-7", "hold_seconds": 30},
            headers=_auth(LabRole.BENCHMARK),
        )
        locked = client.get("/targets/lab-postgres", headers=_auth(LabRole.EXECUTOR)).json()[
            "revision"
        ]
        # The revision is bound to this exact fixture (a digest of its token).
        assert locked == f"pg-gen-1.lock-{_digest('lock-7')}"
        assert "lock-7" not in locked
        wrong = _remediate(
            client,
            action_id="a1",
            action_type="release_lab_db_blocker",
            target_id="lab-postgres",
            expected_revision=f"pg-gen-1.lock-{_digest('lock-8')}",
        )
        assert wrong.json()["error"] == "stale_revision"
        ok = _remediate(
            client,
            action_id="a2",
            action_type="release_lab_db_blocker",
            target_id="lab-postgres",
            expected_revision=locked,
        )
        assert ok.status_code == 201
        assert ok.json()["revision_after"] == "pg-gen-1.lock-none"
        assert (
            client.get("/targets/lab-postgres", headers=_auth(LabRole.READER)).json()["revision"]
            == "pg-gen-1.lock-none"
        )
        # Nothing left to release: a second release is refused as stale.
        again = _remediate(
            client,
            action_id="a3",
            action_type="release_lab_db_blocker",
            target_id="lab-postgres",
            expected_revision="pg-gen-1.lock-none",
        )
        assert again.json()["error"] == "stale_revision"


def test_redis_restart_and_cpu_stop_bump_generations(
    parts: tuple[TestClient, FakeLabApi, FakeRedis],
) -> None:
    client, lab_api, redis = parts
    with client:
        client.post(
            "/faults/redis-outage",
            json={"action_id": "r1", "seconds": 60},
            headers=_auth(LabRole.BENCHMARK),
        )
        assert redis.paused
        ok = _remediate(
            client,
            action_id="a1",
            action_type="restart_lab_dependency",
            target_id="lab-redis",
            expected_revision="redis-gen-1",
        )
        assert ok.json()["revision_after"] == "redis-gen-2"
        assert not redis.paused

        client.post("/faults/cpu-load", json={"action_id": "c1"}, headers=_auth(LabRole.BENCHMARK))
        revision = client.get("/targets/lab-cpu-load", headers=_auth(LabRole.READER)).json()[
            "revision"
        ]
        stopped = _remediate(
            client,
            action_id="a2",
            action_type="stop_lab_cpu_load",
            target_id="lab-cpu-load",
            expected_revision=revision,
        )
        assert stopped.status_code == 201
    assert {"cpu_load": False} in lab_api.calls


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (
            {
                "action_id": "x",
                "action_type": "run_shell",
                "target_id": "lab-api",
                "expected_revision": "rel-1",
            },
            422,
        ),
        (
            {
                "action_id": "x",
                "action_type": "rollback_demo_api",
                "target_id": "lab-redis",
                "expected_revision": "redis-gen-1",
            },
            422,
        ),
    ],
)
def test_unknown_remediations_and_wrong_targets_are_refused(
    parts: tuple[TestClient, FakeLabApi, FakeRedis], body: dict[str, str], code: int
) -> None:
    client, _, _ = parts
    assert _remediate(client, LabRole.EXECUTOR, **body).status_code == code


@pytest.mark.parametrize("role", [LabRole.READER, LabRole.BENCHMARK])
def test_only_the_executor_can_remediate(
    parts: tuple[TestClient, FakeLabApi, FakeRedis], role: LabRole
) -> None:
    client, _, _ = parts
    response = _remediate(
        client,
        role,
        action_id="x",
        action_type="restart_lab_dependency",
        target_id="lab-redis",
        expected_revision="redis-gen-1",
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "path",
    [
        "/faults/deploy",
        "/faults/cpu-load",
        "/faults/noise",
        "/faults/redis-outage",
        "/faults/kafka-outage",
        "/faults/postgres-outage",
        "/faults/error-path",
    ],
)
@pytest.mark.parametrize("role", [LabRole.READER, LabRole.EXECUTOR])
def test_only_the_benchmark_can_inject(
    parts: tuple[TestClient, FakeLabApi, FakeRedis], path: str, role: LabRole
) -> None:
    client, _, _ = parts
    body = (
        {"action_id": "x", "profile": "faulty"} if path.endswith("deploy") else {"action_id": "x"}
    )
    assert client.post(path, json=body, headers=_auth(role)).status_code == 403


def test_operation_lookup_and_ledger_survive_restart(
    parts: tuple[TestClient, FakeLabApi, FakeRedis], tmp_path: Path
) -> None:
    client, _, _ = parts
    _remediate(
        client,
        action_id="keep",
        action_type="restart_lab_dependency",
        target_id="lab-redis",
        expected_revision="redis-gen-1",
    )
    assert (
        client.get("/remediations/operations/keep", headers=_auth(LabRole.EXECUTOR)).status_code
        == 200
    )
    assert (
        client.get("/remediations/operations/keep", headers=_auth(LabRole.READER)).status_code
        == 403
    )
    assert (
        client.get("/remediations/operations/none", headers=_auth(LabRole.EXECUTOR)).status_code
        == 404
    )
    assert Ledger(tmp_path / "ledger.sqlite3").get("keep") is not None


def test_reset_restores_healthy_baseline(parts: tuple[TestClient, FakeLabApi, FakeRedis]) -> None:
    client, lab_api, redis = parts
    with client:
        client.post(
            "/faults/deploy",
            json={"action_id": "d", "profile": "leaky"},
            headers=_auth(LabRole.BENCHMARK),
        )
        client.post(
            "/faults/redis-outage", json={"action_id": "r"}, headers=_auth(LabRole.BENCHMARK)
        )
        client.post("/faults/reset", headers=_auth(LabRole.BENCHMARK))
        assert (
            client.get("/targets/lab-api", headers=_auth(LabRole.READER)).json()["revision"]
            == "rel-1"
        )
    assert not redis.paused
    assert {"reset": True} in lab_api.calls


@pytest.mark.parametrize(
    ("path", "target_id", "before", "after"),
    [
        ("/faults/kafka-outage", "lab-kafka", "kafka-gen-1", "kafka-gen-2"),
        ("/faults/postgres-outage", "lab-postgres", "pg-gen-1.lock-none", "pg-gen-2.lock-none"),
    ],
)
def test_dependency_outages_are_bounded_and_restartable(
    outages: tuple[TestClient, FakeOutage, FakeOutage],
    path: str,
    target_id: str,
    before: str,
    after: str,
) -> None:
    client, kafka, postgres = outages
    outage = kafka if target_id == "lab-kafka" else postgres
    with client:
        injected = client.post(
            path, json={"action_id": "o1", "seconds": 420}, headers=_auth(LabRole.BENCHMARK)
        )
        assert injected.status_code == 201
        assert (outage.active, outage.seconds) == (True, 420)
        # Readers see a neutral generation, never the injected fault.
        target = client.get(f"/targets/{target_id}", headers=_auth(LabRole.READER)).json()
        assert target == {"target_id": target_id, "revision": before}
        ok = _remediate(
            client,
            action_id="fix",
            action_type="restart_lab_dependency",
            target_id=target_id,
            expected_revision=before,
        )
        assert ok.status_code == 201
        assert ok.json()["revision_after"] == after
        assert not outage.active


def test_reset_ends_dependency_outages(
    outages: tuple[TestClient, FakeOutage, FakeOutage],
) -> None:
    client, kafka, postgres = outages
    with client:
        for path in ("/faults/kafka-outage", "/faults/postgres-outage"):
            client.post(path, json={"action_id": "o"}, headers=_auth(LabRole.BENCHMARK))
        assert kafka.active
        assert postgres.active
        client.post("/faults/reset", headers=_auth(LabRole.BENCHMARK))
    assert not kafka.active
    assert not postgres.active


def test_restart_is_refused_for_unregistered_dependency_targets(
    outages: tuple[TestClient, FakeOutage, FakeOutage],
) -> None:
    client, _, _ = outages
    for target in ("lab-api", "lab-cpu-load", "lab-kafka-proxy"):
        response = _remediate(
            client,
            action_id=f"x-{target}",
            action_type="restart_lab_dependency",
            target_id=target,
            expected_revision="rel-1",
        )
        assert response.status_code == 422


def test_error_path_injection_reaches_the_api_without_a_deploy(
    parts: tuple[TestClient, FakeLabApi, FakeRedis],
) -> None:
    client, lab_api, _ = parts
    with client:
        response = client.post(
            "/faults/error-path", json={"action_id": "e1"}, headers=_auth(LabRole.BENCHMARK)
        )
        assert response.status_code == 201
        assert (
            client.get("/deployments/lab-api", headers=_auth(LabRole.READER)).json()[
                "current_release"
            ]
            == HEALTHY_RELEASE
        )
    assert {"error_path": True} in lab_api.calls
