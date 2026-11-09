"""Unit tests for the consumer pause/resume controller (ADR-010)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from fault_lab.auth import LabRole
from fault_lab.consumer.controller import (
    ActionIdConflictError,
    ConsumerController,
    ControlError,
    ForbiddenOperationError,
    Operation,
    OperationRecord,
    StaleGenerationError,
    UnknownOperationError,
    parse_generation,
)


def _pause(ctrl: ConsumerController, action_id: str = "p1", gen: str = "gen-1") -> None:
    ctrl.apply(
        Operation.PAUSE, role=LabRole.BENCHMARK, action_id=action_id, expected_generation=gen
    )


def test_initial_state_is_running_generation_one(controller: ConsumerController) -> None:
    status = controller.status()
    assert (status.paused, status.generation) == (False, "gen-1")


def test_pause_then_resume_bumps_generation(controller: ConsumerController) -> None:
    _pause(controller)
    assert controller.status().paused
    assert controller.status().generation == "gen-2"
    record, replayed = controller.apply(
        Operation.RESUME,
        role=LabRole.EXECUTOR,
        action_id="act_1",
        expected_generation="gen-2",
        desired_paused=False,
    )
    assert not replayed
    assert record.state_changed
    assert (record.generation_before, record.generation_after) == ("gen-2", "gen-3")
    assert not controller.status().paused


@pytest.mark.parametrize(
    ("operation", "role"),
    [
        (Operation.PAUSE, LabRole.EXECUTOR),
        (Operation.PAUSE, LabRole.READER),
        (Operation.RESET, LabRole.EXECUTOR),
        (Operation.RESET, LabRole.READER),
        (Operation.RESUME, LabRole.BENCHMARK),
        (Operation.RESUME, LabRole.READER),
    ],
)
def test_role_separation(
    controller: ConsumerController, operation: Operation, role: LabRole
) -> None:
    with pytest.raises(ForbiddenOperationError):
        controller.apply(
            operation,
            role=role,
            action_id="x",
            expected_generation="gen-1",
            desired_paused=False,
        )
    assert controller.status().generation == "gen-1"


def test_stale_generation_rejected_without_mutation(controller: ConsumerController) -> None:
    _pause(controller)
    with pytest.raises(StaleGenerationError):
        controller.apply(
            Operation.RESUME,
            role=LabRole.EXECUTOR,
            action_id="act_1",
            expected_generation="gen-1",
            desired_paused=False,
        )
    assert controller.status().paused
    with pytest.raises(UnknownOperationError):
        controller.get_operation("act_1")


def test_resume_requires_desired_unpaused(controller: ConsumerController) -> None:
    _pause(controller)
    with pytest.raises(ControlError, match="desired_paused=false"):
        controller.apply(
            Operation.RESUME,
            role=LabRole.EXECUTOR,
            action_id="act_1",
            expected_generation="gen-2",
            desired_paused=True,
        )


def test_repeat_with_same_action_id_replays_receipt(controller: ConsumerController) -> None:
    _pause(controller)

    def resume() -> tuple[OperationRecord, bool]:
        return controller.apply(
            Operation.RESUME,
            role=LabRole.EXECUTOR,
            action_id="act_1",
            expected_generation="gen-2",
            desired_paused=False,
        )

    first, replayed_first = resume()
    second, replayed_second = resume()
    assert (replayed_first, replayed_second) == (False, True)
    assert first == second
    assert controller.status().generation == "gen-3"


def test_same_action_id_different_payload_conflicts(controller: ConsumerController) -> None:
    _pause(controller)
    with pytest.raises(ActionIdConflictError):
        _pause(controller, action_id="p1", gen="gen-2")


def test_resume_when_running_is_target_state_idempotent(controller: ConsumerController) -> None:
    record, _ = controller.apply(
        Operation.RESUME,
        role=LabRole.EXECUTOR,
        action_id="act_noop",
        expected_generation="gen-1",
        desired_paused=False,
    )
    assert not record.state_changed
    assert controller.status().generation == "gen-1"


def test_reset_always_starts_new_generation(controller: ConsumerController) -> None:
    _pause(controller)
    record, _ = controller.apply(
        Operation.RESET, role=LabRole.BENCHMARK, action_id="r1", expected_generation=None
    )
    assert record.generation_after == "gen-3"
    assert not controller.status().paused


def test_operations_survive_restart(ledger_path: Path) -> None:
    first = ConsumerController("persist-demo-consumer", ledger_path)
    _pause(first)
    first.close()
    restarted = ConsumerController("persist-demo-consumer", ledger_path)
    assert restarted.status().paused
    assert restarted.get_operation("p1").generation_after == "gen-2"


def test_concurrent_resumes_apply_once(controller: ConsumerController) -> None:
    _pause(controller)
    results: list[str] = []
    barrier = threading.Barrier(8)

    def resume(index: int) -> None:
        barrier.wait()
        try:
            controller.apply(
                Operation.RESUME,
                role=LabRole.EXECUTOR,
                action_id=f"act_{index}",
                expected_generation="gen-2",
                desired_paused=False,
            )
            results.append("ok")
        except StaleGenerationError:
            results.append("stale")

    threads = [threading.Thread(target=resume, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count("ok") == 1
    assert controller.status().generation == "gen-3"


@pytest.mark.parametrize("value", ["gen-x", "8", "generation-8", "gen-"])
def test_malformed_generation(value: str) -> None:
    with pytest.raises(ControlError):
        parse_generation(value)
