"""Pause/resume controller for the test consumer (ADR-009, ADR-010).

The controller owns the consumer's desired paused state, a monotonically
increasing generation and an operation ledger keyed by ``action_id``. It is
pure Python plus SQLite so it can be unit-tested without Kafka, and so
receipts survive a worker restart.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from fault_lab.auth import LabRole


class Operation(StrEnum):
    PAUSE = "pause"
    RESUME = "resume"
    RESET = "reset"


# Which lab role may perform which operation. Pause/reset are fault-injection
# privileges of the benchmark controller; resume is the executor's single
# remediation privilege. Readers may not mutate anything.
OPERATION_ROLES: dict[Operation, LabRole] = {
    Operation.PAUSE: LabRole.BENCHMARK,
    Operation.RESUME: LabRole.EXECUTOR,
    Operation.RESET: LabRole.BENCHMARK,
}


class ControlError(Exception):
    status_code = 400
    code = "bad_request"


class ForbiddenOperationError(ControlError):
    status_code = 403
    code = "forbidden"


class StaleGenerationError(ControlError):
    status_code = 409
    code = "stale_generation"


class ActionIdConflictError(ControlError):
    status_code = 409
    code = "action_id_conflict"


class UnknownOperationError(ControlError):
    status_code = 404
    code = "operation_not_found"


def render_generation(value: int) -> str:
    return f"gen-{value}"


def parse_generation(value: str) -> int:
    prefix, _, number = value.partition("-")
    if prefix != "gen" or not number.isdigit():
        raise ControlError(f"malformed generation: {value!r}")
    return int(number)


@dataclass(frozen=True)
class OperationRecord:
    action_id: str
    operation: Operation
    requested_by: LabRole
    desired_paused: bool
    generation_before: str
    generation_after: str
    state_changed: bool
    status: str
    request_digest: str
    created_at: str

    def to_json(self) -> dict[str, Any]:
        return {k: (v.value if isinstance(v, StrEnum) else v) for k, v in asdict(self).items()}


@dataclass(frozen=True)
class ConsumerStatus:
    consumer_id: str
    paused: bool
    generation: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS consumer_state (
    consumer_id TEXT PRIMARY KEY,
    paused INTEGER NOT NULL,
    generation INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS operations (
    action_id TEXT PRIMARY KEY,
    record TEXT NOT NULL
);
"""


class ConsumerController:
    """Thread-safe, durable controller for one registered consumer."""

    def __init__(self, consumer_id: str, ledger_path: Path | str) -> None:
        self.consumer_id = consumer_id
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(ledger_path), check_same_thread=False, isolation_level=None)
        self._db.executescript(_SCHEMA)
        self._db.execute(
            "INSERT OR IGNORE INTO consumer_state (consumer_id, paused, generation)"
            " VALUES (?, 0, 1)",
            (consumer_id,),
        )

    def close(self) -> None:
        self._db.close()

    def status(self) -> ConsumerStatus:
        with self._lock:
            paused, generation = self._read_state()
        return ConsumerStatus(self.consumer_id, paused, render_generation(generation))

    def get_operation(self, action_id: str) -> OperationRecord:
        with self._lock:
            row = self._db.execute(
                "SELECT record FROM operations WHERE action_id = ?", (action_id,)
            ).fetchone()
        if row is None:
            raise UnknownOperationError(action_id)
        return _decode(row[0])

    def apply(
        self,
        operation: Operation,
        *,
        role: LabRole,
        action_id: str,
        expected_generation: str | None,
        desired_paused: bool | None = None,
    ) -> tuple[OperationRecord, bool]:
        """Apply an operation; return ``(record, replayed)``.

        A repeated request with the same ``action_id`` and payload returns the
        stored receipt without changing state again. The same ``action_id``
        with a different payload is rejected.
        """
        if OPERATION_ROLES[operation] is not role:
            raise ForbiddenOperationError(f"{role.value} may not {operation.value}")
        target_paused = _target_paused(operation, desired_paused)
        digest = _request_digest(operation, action_id, expected_generation, target_paused)

        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                existing = self._db.execute(
                    "SELECT record FROM operations WHERE action_id = ?", (action_id,)
                ).fetchone()
                if existing is not None:
                    record = _decode(existing[0])
                    if record.request_digest != digest:
                        raise ActionIdConflictError(action_id)
                    self._db.execute("COMMIT")
                    return record, True

                paused, generation = self._read_state()
                if operation is not Operation.RESET:
                    if expected_generation is None:
                        raise StaleGenerationError("expected_generation is required")
                    if parse_generation(expected_generation) != generation:
                        raise StaleGenerationError(
                            f"expected {expected_generation}, current "
                            f"{render_generation(generation)}"
                        )
                # Target-state idempotence: converging to the current state is
                # recorded but does not bump the generation (except reset,
                # which always starts a fresh generation for the next trial).
                changed = paused != target_paused or operation is Operation.RESET
                new_generation = generation + 1 if changed else generation
                self._db.execute(
                    "UPDATE consumer_state SET paused = ?, generation = ? WHERE consumer_id = ?",
                    (int(target_paused), new_generation, self.consumer_id),
                )
                record = OperationRecord(
                    action_id=action_id,
                    operation=operation,
                    requested_by=role,
                    desired_paused=target_paused,
                    generation_before=render_generation(generation),
                    generation_after=render_generation(new_generation),
                    state_changed=paused != target_paused,
                    status="COMPLETED",
                    request_digest=digest,
                    created_at=datetime.now(UTC).isoformat(),
                )
                self._db.execute(
                    "INSERT INTO operations (action_id, record) VALUES (?, ?)",
                    (action_id, json.dumps(record.to_json())),
                )
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return record, False

    def _read_state(self) -> tuple[bool, int]:
        row = self._db.execute(
            "SELECT paused, generation FROM consumer_state WHERE consumer_id = ?",
            (self.consumer_id,),
        ).fetchone()
        return bool(row[0]), int(row[1])


def _target_paused(operation: Operation, desired_paused: bool | None) -> bool:
    if operation is Operation.PAUSE:
        return True
    if operation is Operation.RESET:
        return False
    # The executor's only permitted target state is "running".
    if desired_paused is not False:
        raise ControlError("resume requires desired_paused=false")
    return False


def _request_digest(
    operation: Operation, action_id: str, expected: str | None, paused: bool
) -> str:
    canonical = json.dumps(
        {"op": operation.value, "action_id": action_id, "expected": expected, "paused": paused},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _decode(raw: str) -> OperationRecord:
    data = json.loads(raw)
    data["operation"] = Operation(data["operation"])
    data["requested_by"] = LabRole(data["requested_by"])
    return OperationRecord(**data)
