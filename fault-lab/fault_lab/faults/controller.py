"""Fault-lab controller state: fixtures, deployments and executor remediations.

Two very different callers use it:

* the **benchmark controller** (benchmark token) injects and resets faults;
* the **IncidentOps executor** (executor token) may only invoke the four
  allowlisted remediations, each addressed by a registered target, a pinned
  expected revision and a stable ``action_id`` recorded in a durable ledger.

Readers (agent tools, verifier) may see target revisions and the deployment
timeline, which are ordinary operational facts; injected fault kinds are
never exposed.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol

from fault_lab.faults.app import DbLockFixture, FixtureNotFoundError

HEALTHY_RELEASE: Final = "rel-1"
NO_BLOCKER: Final = "none"


class RemediationType(StrEnum):
    RELEASE_LAB_DB_BLOCKER = "release_lab_db_blocker"
    RESTART_LAB_DEPENDENCY = "restart_lab_dependency"
    ROLLBACK_DEMO_API = "rollback_demo_api"
    STOP_LAB_CPU_LOAD = "stop_lab_cpu_load"


# Each remediation applies to exactly one registered target.
REMEDIATION_TARGETS: Final = {
    RemediationType.RELEASE_LAB_DB_BLOCKER: "lab-postgres",
    RemediationType.RESTART_LAB_DEPENDENCY: "lab-redis",
    RemediationType.ROLLBACK_DEMO_API: "lab-api",
    RemediationType.STOP_LAB_CPU_LOAD: "lab-cpu-load",
}


class RemediationError(Exception):
    status_code = 400
    code = "bad_request"


class StaleRevisionError(RemediationError):
    status_code = 409
    code = "stale_revision"


class ActionIdConflictError(RemediationError):
    status_code = 409
    code = "action_id_conflict"


class WrongTargetError(RemediationError):
    status_code = 422
    code = "target_not_allowed"


class LabApiControl(Protocol):
    async def update(self, **fields: Any) -> dict[str, Any]: ...


class RedisControl(Protocol):
    async def pause(self, milliseconds: int) -> None: ...
    async def unpause(self) -> None: ...


@dataclass
class Deployment:
    release_id: str
    deployed_at: str
    actor: str


@dataclass
class LabState:
    current_release: str = HEALTHY_RELEASE
    next_release: int = 2
    deployments: list[Deployment] = field(default_factory=list)
    redis_generation: int = 1
    redis_paused: bool = False
    cpu_generation: int = 1
    cpu_running: bool = False


class ReceiptStore(Protocol):
    def get(self, action_id: str) -> dict[str, Any] | None: ...
    def put(self, action_id: str, record: dict[str, Any]) -> None: ...


class Ledger:
    """Durable remediation receipts keyed by action_id (SQLite on the lab volume)."""

    def __init__(self, path: Path | str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS remediations (action_id TEXT PRIMARY KEY, record TEXT)"
        )

    def get(self, action_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT record FROM remediations WHERE action_id = ?", (action_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, action_id: str, record: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO remediations (action_id, record) VALUES (?, ?)",
                (action_id, json.dumps(record, sort_keys=True)),
            )


def _now() -> str:
    return datetime.now(UTC).isoformat()


class LabController:
    def __init__(
        self,
        db_lock: DbLockFixture,
        lab_api: LabApiControl,
        redis: RedisControl,
        ledger: ReceiptStore,
    ) -> None:
        self._db_lock = db_lock
        self._lab_api = lab_api
        self._redis = redis
        self._ledger = ledger
        self.state = LabState()
        self._mutex = asyncio_lock()

    # ----- read-only views (reader, executor and benchmark roles) -------------

    def target_revision(self, target_id: str) -> str | None:
        if target_id == "lab-postgres":
            lock = self._db_lock.status()["db_lock"]
            return str(lock["fixture_token"]) if lock else NO_BLOCKER
        if target_id == "lab-redis":
            return f"redis-gen-{self.state.redis_generation}"
        if target_id == "lab-api":
            return self.state.current_release
        if target_id == "lab-cpu-load":
            return f"load-gen-{self.state.cpu_generation}"
        return None

    def deployments(self) -> dict[str, Any]:
        return {
            "service_id": "lab-api",
            "current_release": self.state.current_release,
            "pinned_healthy_release": HEALTHY_RELEASE,
            "history": [d.__dict__ for d in self.state.deployments[-20:]],
        }

    def operation(self, action_id: str) -> dict[str, Any] | None:
        return self._ledger.get(action_id)

    # ----- benchmark injections ---------------------------------------------

    async def deploy_release(self, profile: str) -> dict[str, Any]:
        async with self._mutex:
            release = f"rel-{self.state.next_release}"
            self.state.next_release += 1
            await self._lab_api.update(release_id=release, profile=profile)
            self._record_deploy(release, actor="deploy-pipeline")
            return self.deployments()

    async def pause_redis(self, seconds: float) -> None:
        async with self._mutex:
            await self._redis.pause(int(seconds * 1000))
            self.state.redis_paused = True

    async def start_cpu_load(self) -> None:
        async with self._mutex:
            await self._lab_api.update(cpu_load=True)
            self.state.cpu_running = True
            self.state.cpu_generation += 1

    async def start_noise(self, seconds: float) -> None:
        await self._lab_api.update(noise_seconds=seconds)

    async def reset(self) -> None:
        async with self._mutex:
            await self._db_lock.reset()
            await self._redis.unpause()
            await self._lab_api.update(reset=True)
            if self.state.current_release != HEALTHY_RELEASE:
                self._record_deploy(HEALTHY_RELEASE, actor="lab-reset")
            self.state.redis_paused = False
            self.state.cpu_running = False
            self.state.redis_generation += 1
            self.state.cpu_generation += 1

    def _record_deploy(self, release: str, actor: str) -> None:
        self.state.current_release = release
        self.state.deployments.append(Deployment(release, _now(), actor))

    # ----- executor remediations ---------------------------------------------

    async def remediate(
        self,
        action_id: str,
        action_type: RemediationType,
        target_id: str,
        expected_revision: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        """Apply one allowlisted remediation; returns ``(receipt, replayed)``."""
        if REMEDIATION_TARGETS[action_type] != target_id:
            raise WrongTargetError(f"{action_type.value} cannot target {target_id}")
        request = {
            "action_type": action_type.value,
            "target_id": target_id,
            "expected_revision": expected_revision,
            "arguments": arguments,
        }
        async with self._mutex:
            existing = self._ledger.get(action_id)
            if existing is not None:
                if existing["request"] != request:
                    raise ActionIdConflictError(action_id)
                return existing, True
            current = self.target_revision(target_id)
            if current != expected_revision:
                raise StaleRevisionError(f"expected {expected_revision}, current {current}")
            await self._apply(action_type, expected_revision, arguments)
            receipt = {
                "action_id": action_id,
                "status": "COMPLETED",
                "request": request,
                "revision_before": current,
                "revision_after": self.target_revision(target_id),
                "completed_at": _now(),
            }
            self._ledger.put(action_id, receipt)
            return receipt, False

    async def _apply(
        self, action_type: RemediationType, revision: str, arguments: dict[str, Any]
    ) -> None:
        if action_type is RemediationType.RELEASE_LAB_DB_BLOCKER:
            # Only the exact known fixture transaction is ever released.
            try:
                await self._db_lock.release(revision)
            except FixtureNotFoundError as exc:
                raise StaleRevisionError("no active blocker with that token") from exc
        elif action_type is RemediationType.RESTART_LAB_DEPENDENCY:
            await self._redis.unpause()
            self.state.redis_paused = False
            self.state.redis_generation += 1
        elif action_type is RemediationType.ROLLBACK_DEMO_API:
            target = arguments.get("target_release")
            if target != HEALTHY_RELEASE:
                # Never deploy an arbitrary release string: only the pinned one.
                raise RemediationError("rollback target must be the pinned healthy release")
            await self._lab_api.update(release_id=HEALTHY_RELEASE, profile="healthy")
            self._record_deploy(HEALTHY_RELEASE, actor="incidentops-executor")
        elif action_type is RemediationType.STOP_LAB_CPU_LOAD:
            await self._lab_api.update(cpu_load=False)
            self.state.cpu_running = False
            self.state.cpu_generation += 1


def asyncio_lock() -> Any:
    import asyncio

    return asyncio.Lock()
