"""Deterministic confidence rules applied when a diagnosis is validated (ADR-025)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from incidentops.agent.model import ModelTurn, ScriptedModelAdapter, ToolCall
from tests.unit.test_investigation_graph import CONSUMER, lag_and_status, run, submit


def lag_only() -> ModelTurn:
    return ModelTurn(
        tool_calls=[ToolCall("t1", "get_kafka_consumer_lag", {"consumer_group_id": CONSUMER})]
    )


def lag_and_logs() -> ModelTurn:
    return ModelTurn(
        tool_calls=[
            ToolCall("t1", "get_kafka_consumer_lag", {"consumer_group_id": CONSUMER}),
            ToolCall("t2", "search_logs", {"service_id": CONSUMER}),
        ]
    )


def with_confidence(level: str, **diagnosis: Any) -> Any:
    def turn(messages: list[dict[str, Any]]) -> ModelTurn:
        result = submit(messages)
        result.tool_calls[0].arguments["diagnosis"]["confidence_level"] = level
        result.tool_calls[0].arguments["diagnosis"].update(diagnosis)
        return result

    return turn


def no_action(level: str, root_cause: str) -> Any:
    def turn(messages: list[dict[str, Any]]) -> ModelTurn:
        result = submit(messages)
        args = result.tool_calls[0].arguments
        args["diagnosis"].update(confidence_level=level, root_cause_id=root_cause)
        args.pop("proposed_action")
        args["needs_human_review"] = True
        return result

    return turn


async def _rejection(reads: ModelTurn, bad: Any) -> tuple[dict[str, Any], str]:
    model = ScriptedModelAdapter([reads, bad, bad])
    final, _ = await run(model)
    reason = json.loads(model.calls[2][-1]["content"][0]["content"])["reason"]
    return final, reason


async def test_high_confidence_with_symptom_and_status_readings_is_accepted() -> None:
    final, _ = await run(ScriptedModelAdapter([lag_and_status(), with_confidence("high")]))
    assert final["status"] == "COMPLETED"
    assert final["report"]["diagnosis"]["confidence_level"] == "high"


@pytest.mark.parametrize(
    ("reads", "reason"),
    [
        (lag_only(), "at least two different tools"),
        (lag_and_logs(), "dependency, change, status or resource check"),
    ],
)
async def test_high_confidence_needs_corroboration(reads: ModelTurn, reason: str) -> None:
    final, message = await _rejection(reads, with_confidence("high"))
    assert final["status"] == "FAILED"
    assert message.startswith("confidence rule:")
    assert reason in message


async def test_unsupported_high_confidence_can_be_repaired_to_medium() -> None:
    model = ScriptedModelAdapter([lag_only(), with_confidence("high"), with_confidence("medium")])
    final, _ = await run(model)
    assert final["status"] == "COMPLETED"
    assert final["report"]["diagnosis"]["confidence_level"] == "medium"


async def test_low_confidence_may_not_propose_an_action() -> None:
    final, reason = await _rejection(lag_and_status(), with_confidence("low"))
    assert final["status"] == "FAILED"
    assert "may not propose an action" in reason


async def test_insufficient_evidence_must_be_low_confidence() -> None:
    final, reason = await _rejection(lag_and_status(), no_action("medium", "insufficient_evidence"))
    assert final["status"] == "FAILED"
    assert "insufficient_evidence must use confidence_level low" in reason
    ok, _ = await run(
        ScriptedModelAdapter([lag_and_status(), no_action("low", "insufficient_evidence")])
    )
    assert ok["status"] == "COMPLETED"
