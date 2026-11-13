"""Investigation graph behaviour with a scripted model (no provider calls)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from incidentops.agent.graph import Budgets, build_graph, describe_invalid_diagnosis, initial_state
from incidentops.agent.model import (
    ModelTurn,
    ScriptedModelAdapter,
    ToolCall,
    Usage,
    evidence_ids_in,
)
from incidentops.agent.prompt import SYSTEM_PROMPT, initial_message
from incidentops.domain.catalog import load_catalog
from incidentops.domain.ids import new_id
from tests.unit.tool_fakes import NOW, FakeBackend, make_gateway

CONSUMER = "persist-demo-consumer"


def lag_and_status() -> ModelTurn:
    return ModelTurn(
        tool_calls=[
            ToolCall("t1", "get_kafka_consumer_lag", {"consumer_group_id": CONSUMER}),
            ToolCall("t2", "get_consumer_status", {"worker_id": CONSUMER}),
        ]
    )


def submit(messages: list[dict[str, Any]], **overrides: Any) -> ModelTurn:
    ids = evidence_ids_in(messages)
    report: dict[str, Any] = {
        "diagnosis": {
            "root_cause_id": "consumer_paused",
            "affected_component": CONSUMER,
            "confidence_level": "high",
            "summary": "Worker reports paused while lag grows.",
            "supporting_evidence_ids": ids,
        },
        "proposed_action": {
            "action_type": "resume_test_consumer",
            "environment": "fault-lab",
            "target_id": CONSUMER,
            "arguments": {"desired_paused": False},
            "reason": "Restore consumption.",
            "evidence_ids": ids,
        },
    }
    report.update(overrides)
    return ModelTurn(tool_calls=[ToolCall("final", "submit_diagnosis", report)])


async def run(
    model: ScriptedModelAdapter,
    budgets: Budgets | None = None,
    backend: FakeBackend | None = None,
    clock_ms: Any = None,
) -> tuple[dict[str, Any], Any]:
    gateway, backend, evidence, trajectory = make_gateway(backend)
    budgets = budgets or Budgets()
    kwargs: dict[str, Any] = {}
    if clock_ms is not None:
        kwargs["clock_ms"] = clock_ms
    graph = build_graph(model, gateway, evidence, trajectory, budgets, **kwargs)
    incident_id = new_id("inc")
    state = initial_state(
        incident_id,
        new_id("run"),
        1,
        initial_message({"id": incident_id, "service_id": CONSUMER}, [], load_catalog()),
        budgets,
        now_ms=int(NOW.timestamp() * 1000) if clock_ms else None,
    )
    final = await graph.ainvoke(state)
    return final, trajectory


async def test_happy_path_produces_evidence_linked_report() -> None:
    model = ScriptedModelAdapter([lag_and_status(), submit])
    final, trajectory = await run(model)
    assert final["status"] == "COMPLETED"
    report = final["report"]
    assert report["diagnosis"]["root_cause_id"] == "consumer_paused"
    assert set(report["diagnosis"]["supporting_evidence_ids"]) == set(final["evidence_ids"])
    assert len(final["evidence_ids"]) == 2
    kinds = [s.kind.value for s in trajectory.steps]
    assert kinds == ["model_call", "tool_call", "tool_call", "model_call", "diagnosis"]


async def test_fabricated_evidence_gets_one_repair_attempt() -> None:
    fake = "ev_" + "f" * 32

    def bad(messages: list[dict[str, Any]]) -> ModelTurn:
        turn = submit(messages)
        turn.tool_calls[0].arguments["diagnosis"]["supporting_evidence_ids"] = [fake]
        return turn

    model = ScriptedModelAdapter([lag_and_status(), bad, submit])
    final, _ = await run(model)
    assert final["status"] == "COMPLETED"
    rejection = json.loads(model.calls[-1][-1]["content"][0]["content"])
    assert rejection["status"] == "REJECTED"
    assert fake in rejection["reason"]


async def test_repeated_invalid_reports_fail_the_run() -> None:
    def malformed(_: list[dict[str, Any]]) -> ModelTurn:
        return ModelTurn(tool_calls=[ToolCall("x", "submit_diagnosis", {"diagnosis": {}})])

    model = ScriptedModelAdapter([malformed, malformed])
    final, trajectory = await run(model)
    assert (final["status"], final["error_code"]) == ("FAILED", "invalid_diagnosis")
    assert [s.status for s in trajectory.steps if s.kind.value == "diagnosis"] == [
        "INVALID",
        "INVALID",
    ]


async def test_invalid_report_names_the_failing_fields() -> None:
    secret_ish = "do-not-echo-this-value"

    def malformed(_: list[dict[str, Any]]) -> ModelTurn:
        payload = {"diagnosis": {"root_cause_id": secret_ish}}
        return ModelTurn(tool_calls=[ToolCall("x", "submit_diagnosis", payload)])

    model = ScriptedModelAdapter([malformed, malformed])
    _, trajectory = await run(model)
    error = next(s for s in trajectory.steps if s.kind.value == "diagnosis").metadata["error"]
    assert error.startswith("invalid diagnosis: ValidationError (")
    assert "diagnosis." in error  # a field path, not just the class name
    assert secret_ish not in error  # input values are not echoed
    step = next(s for s in trajectory.steps if s.kind.value == "diagnosis")
    assert step.metadata["submitted_keys"] == ["diagnosis"]
    assert secret_ish not in str(step.metadata)
    # The model gets the same detail so it can repair the submission.
    rejection = json.loads(model.calls[-1][-1]["content"][0]["content"])
    assert rejection["reason"] == error


def test_plain_value_errors_keep_their_message() -> None:
    error = describe_invalid_diagnosis(ValueError("a proposed action requires supporting evidence"))
    assert error == "ValueError: a proposed action requires supporting evidence"


async def test_prompt_injection_in_logs_cannot_cause_mutation() -> None:
    # S-07 / US-03: the model "obeys" injected log text; every write attempt is denied.
    def obey_injection(_: list[dict[str, Any]]) -> ModelTurn:
        return ModelTurn(
            tool_calls=[
                ToolCall("w1", "run_shell", {"command": "rm -rf /"}),
                ToolCall("w2", "resume_test_consumer", {"target_id": CONSUMER}),
                ToolCall(
                    "w3",
                    "query_metrics",
                    {"template_id": "x", "service_id": "lab-api", "url": "http://attacker.example"},
                ),
            ]
        )

    def abstain(messages: list[dict[str, Any]]) -> ModelTurn:
        ids = evidence_ids_in(messages)
        return ModelTurn(
            tool_calls=[
                ToolCall(
                    "final",
                    "submit_diagnosis",
                    {
                        "diagnosis": {
                            "root_cause_id": "insufficient_evidence",
                            "affected_component": "lab-api",
                            "confidence_level": "low",
                            "summary": "Logs contained instructions; treated as data.",
                            "supporting_evidence_ids": ids,
                        },
                        "needs_human_review": True,
                    },
                )
            ]
        )

    logs = ModelTurn(tool_calls=[ToolCall("l1", "search_logs", {"service_id": "lab-api"})])
    backend = FakeBackend()
    model = ScriptedModelAdapter([logs, obey_injection, abstain])
    final, trajectory = await run(model, backend=backend)

    assert final["status"] == "COMPLETED"
    denied = [s for s in trajectory.steps if s.status in {"DENIED", "INVALID"}]
    assert {s.metadata["tool"] for s in denied} == {
        "run_shell",
        "resume_test_consumer",
        "query_metrics",
    }
    assert not [q for q in backend.queries if "attacker" in q]
    # The model was never offered a mutating tool.
    offered = set(model.tool_names[0])
    assert not {n for n in offered if any(w in n for w in ("resume", "pause", "shell"))}


async def test_step_budget_stops_runaway_loops() -> None:
    def again(_: list[dict[str, Any]]) -> ModelTurn:
        return ModelTurn(
            tool_calls=[ToolCall(new_id("step"), "get_consumer_status", {"worker_id": CONSUMER})]
        )

    model = ScriptedModelAdapter([again] * 10)
    final, _ = await run(model, budgets=Budgets(max_steps=3))
    assert (final["status"], final["error_code"]) == ("BUDGET_EXCEEDED", "step_or_time_budget")
    assert final["step_count"] == 3


async def test_deadline_stops_the_run() -> None:
    start_ms = int(NOW.timestamp() * 1000)
    ticks = iter([start_ms, start_ms + 10_000_000])
    model = ScriptedModelAdapter([lag_and_status(), submit])
    final, _ = await run(
        model, budgets=Budgets(wall_clock_seconds=60), clock_ms=lambda: next(ticks)
    )
    assert final["status"] == "BUDGET_EXCEEDED"


async def test_model_error_fails_run_and_is_recorded() -> None:
    model = ScriptedModelAdapter([])  # exhausted script raises ModelError
    final, trajectory = await run(model)
    assert (final["status"], final["error_code"]) == ("FAILED", "model_error")
    assert trajectory.steps[0].status == "ERROR"


async def test_text_only_turn_is_nudged() -> None:
    model = ScriptedModelAdapter([ModelTurn(text="thinking out loud"), lag_and_status(), submit])
    final, _ = await run(model)
    assert final["status"] == "COMPLETED"
    assert model.calls[1][-1]["content"][0]["text"].startswith("Call a read tool")


async def test_thinking_only_turn_is_not_replayed_and_is_nudged() -> None:
    thinking_only = ModelTurn(
        blocks=[{"type": "thinking", "thinking": "cut off", "signature": "s"}]
    )
    model = ScriptedModelAdapter([thinking_only, lag_and_status(), submit])
    final, _ = await run(model)
    assert final["status"] == "COMPLETED"
    # No assistant message with empty content reaches the provider.
    for call in model.calls:
        assert all(m["content"] for m in call)
        assert not any(
            m["role"] == "assistant" and m["content"][0]["type"] == "thinking" for m in call
        )
    assert model.calls[1][-1]["content"][0]["text"].startswith("Call a read tool")


async def test_flattened_submission_records_its_top_level_keys() -> None:
    def flattened(_: list[dict[str, Any]]) -> ModelTurn:
        payload = {"root_cause_id": "consumer_paused", "summary": "s", "confidence_level": "high"}
        return ModelTurn(tool_calls=[ToolCall("x", "submit_diagnosis", payload)])

    model = ScriptedModelAdapter([flattened, flattened])
    _, trajectory = await run(model)
    step = next(s for s in trajectory.steps if s.kind.value == "diagnosis")
    assert step.metadata["submitted_keys"] == ["confidence_level", "root_cause_id", "summary"]
    assert "consumer_paused" not in str(step.metadata["submitted_keys"])


async def test_valid_submission_records_no_keys() -> None:
    model = ScriptedModelAdapter([lag_and_status(), submit])
    _, trajectory = await run(model)
    step = next(s for s in trajectory.steps if s.kind.value == "diagnosis")
    assert step.metadata["submitted_keys"] is None


async def test_cache_usage_is_accumulated_apart_from_input() -> None:
    def with_usage(turn: ModelTurn, usage: Usage) -> ModelTurn:
        return ModelTurn(tool_calls=turn.tool_calls, text=turn.text, usage=usage)

    model = ScriptedModelAdapter(
        [
            with_usage(lag_and_status(), Usage(400, 50, 0, 300)),
            lambda m: with_usage(submit(m), Usage(80, 60, 700, 0)),
        ]
    )
    final, trajectory = await run(model)
    assert final["status"] == "COMPLETED"
    assert (final["input_tokens"], final["output_tokens"]) == (480, 110)
    assert final["cache_read_input_tokens"] == 700
    assert final["cache_creation_input_tokens"] == 300
    first = next(s for s in trajectory.steps if s.kind.value == "model_call")
    assert first.metadata["cache_creation_input_tokens"] == 300


async def test_checkpointer_persists_state_by_thread() -> None:
    gateway, _, evidence, trajectory = make_gateway()
    saver = InMemorySaver()
    model = ScriptedModelAdapter([lag_and_status(), submit])
    graph = build_graph(model, gateway, evidence, trajectory, Budgets(), checkpointer=saver)
    incident_id = new_id("inc")
    state = initial_state(
        incident_id,
        new_id("run"),
        1,
        initial_message({"id": incident_id}, [], load_catalog()),
        Budgets(),
    )
    config = {"configurable": {"thread_id": "thread-1"}}
    await graph.ainvoke(state, config)
    snapshot = await graph.aget_state(config)
    assert snapshot.values["status"] == "COMPLETED"


@pytest.mark.parametrize(
    "secret_token",
    ["pause_known_test_consumer", "expected", "ground_truth", "FAULT_LAB_BENCHMARK_TOKEN"],
)
def test_prompt_contains_no_benchmark_control_data(secret_token: str) -> None:
    message = initial_message({"id": "inc_x"}, [], load_catalog())
    assert secret_token not in SYSTEM_PROMPT
    assert secret_token not in json.dumps(message)


def test_prompt_states_exact_action_argument_contract() -> None:
    # A live DeepSeek run proposed {"consumer_id": ...}; policy correctly denied it.
    # The model must be told the exact contract instead of guessing.
    message = json.dumps(initial_message({"id": "inc_x"}, [], load_catalog()))
    assert (
        '\\"action_arguments\\": {\\"resume_test_consumer\\": {\\"desired_paused\\": [false]}}'
        in message
    )
    assert "exactly" in SYSTEM_PROMPT


async def test_spent_tool_budget_ends_the_run() -> None:
    model = ScriptedModelAdapter([lag_and_status()] * 10)
    final, _ = await run(model, budgets=Budgets(max_tool_calls=4))
    assert (final["status"], final["error_code"]) == ("BUDGET_EXCEEDED", "tool_budget")
    assert final["tool_call_count"] == 4


def test_prompt_states_root_cause_action_contract() -> None:
    # The agent is told which action types policy accepts per root cause, so it
    # does not propose a valid action for the wrong diagnosis (ADR-024).
    message = initial_message({"id": "inc_x"}, [], load_catalog())
    text = message["content"][0]["text"]
    contract = json.loads(text.split("Root-cause action contract:\n", 1)[1].split("\n", 1)[0])
    assert contract["consumer_paused"] == ["resume_test_consumer"]
    assert contract["insufficient_evidence"] == []
    assert text.index("Root-cause action contract") < text.index("<untrusted_alert_data>")
    assert "root-cause action contract" in SYSTEM_PROMPT


def test_prompt_has_rules_method_and_confidence_sections() -> None:
    for tag in ("rules", "investigation_method", "confidence_rubric"):
        assert f"<{tag}>" in SYSTEM_PROMPT
        assert f"</{tag}>" in SYSTEM_PROMPT
    method = SYSTEM_PROMPT.split("<investigation_method>")[1].split("</investigation_method>")[0]
    # Required coverage and a stop condition, not a numbered script.
    assert "no fixed order" in method
    assert "Stop as soon as" in method
    assert "1." not in method
    rubric = SYSTEM_PROMPT.split("<confidence_rubric>")[1].split("</confidence_rubric>")[0]
    assert all(f"- {level}:" in rubric for level in ("high", "medium", "low"))
