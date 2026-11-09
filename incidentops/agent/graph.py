"""LangGraph investigation graph (architecture §6.1).

    agent --(read tool calls)--> tools --> agent
    agent --(submit_diagnosis)--> validate --> END | agent (one repair attempt)
    agent --(budget or deadline exhausted)--> END

The graph only reads: tools go through the ToolGateway, and the final output is
a validated ``DiagnosisReport`` artifact. Nothing in the graph can mutate an
external system, so replaying a node after a checkpoint resume is safe.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from incidentops.agent.model import SUBMIT_DIAGNOSIS, ModelAdapter, ModelError, ModelTurn
from incidentops.agent.prompt import SYSTEM_PROMPT, submit_diagnosis_tool
from incidentops.domain.schemas import DiagnosisReport
from incidentops.evidence.store import EvidenceSink, UnresolvedEvidenceError
from incidentops.time import utcnow
from incidentops.tools.gateway import RunContext, ToolGateway
from incidentops.trajectory.recorder import StepKind, StepRecord, TrajectorySink

RunStatus = Literal["RUNNING", "COMPLETED", "FAILED", "BUDGET_EXCEEDED"]


class InvestigationState(TypedDict):
    incident_id: str
    run_id: str
    incident_snapshot_version: int
    status: RunStatus
    messages: list[dict[str, Any]]
    evidence_ids: list[str]
    step_count: int
    tool_call_count: int
    input_tokens: int
    output_tokens: int
    deadline_epoch_ms: int
    repair_attempts: int
    report: dict[str, Any] | None
    error_code: str | None


@dataclass(frozen=True)
class Budgets:
    """Defaults from NFR-005; configurable per run."""

    max_steps: int = 20
    max_tool_calls: int = 40
    wall_clock_seconds: int = 300
    max_repair_attempts: int = 1


def initial_state(
    incident_id: str,
    run_id: str,
    incident_version: int,
    first_message: dict[str, Any],
    budgets: Budgets,
    now_ms: int | None = None,
) -> InvestigationState:
    start_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    return InvestigationState(
        incident_id=incident_id,
        run_id=run_id,
        incident_snapshot_version=incident_version,
        status="RUNNING",
        messages=[first_message],
        evidence_ids=[],
        step_count=0,
        tool_call_count=0,
        input_tokens=0,
        output_tokens=0,
        deadline_epoch_ms=start_ms + budgets.wall_clock_seconds * 1000,
        repair_attempts=0,
        report=None,
        error_code=None,
    )


def _tool_result(tool_use_id: str, body: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": json.dumps(body, sort_keys=True),
        "is_error": is_error,
    }


def build_graph(
    model: ModelAdapter,
    gateway: ToolGateway,
    evidence: EvidenceSink,
    trajectory: TrajectorySink,
    budgets: Budgets,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    clock_ms: Callable[[], int] = lambda: int(time.time() * 1000),
) -> Any:
    tools = [*gateway.manifest(), submit_diagnosis_tool()]
    contexts: dict[str, RunContext] = {}

    def run_context(state: InvestigationState) -> RunContext:
        # Budget counters are rebuilt from the checkpointed state on resume.
        ctx = contexts.get(state["run_id"])
        if ctx is None:
            ctx = RunContext(
                incident_id=state["incident_id"],
                run_id=state["run_id"],
                max_tool_calls=budgets.max_tool_calls,
            )
            ctx.total_calls = state["tool_call_count"]
            contexts[state["run_id"]] = ctx
        return ctx

    async def agent(state: InvestigationState) -> dict[str, Any]:
        if state["step_count"] >= budgets.max_steps or clock_ms() >= state["deadline_epoch_ms"]:
            return {"status": "BUDGET_EXCEEDED", "error_code": "step_or_time_budget"}
        started = utcnow()
        try:
            turn: ModelTurn = await model.next_turn(SYSTEM_PROMPT, state["messages"], tools)
        except ModelError as exc:
            trajectory.record(
                StepRecord(
                    state["run_id"],
                    StepKind.MODEL_CALL,
                    "ERROR",
                    started,
                    utcnow(),
                    {"model_id": model.model_id, "error": str(exc)},
                )
            )
            return {"status": "FAILED", "error_code": "model_error"}
        trajectory.record(
            StepRecord(
                state["run_id"],
                StepKind.MODEL_CALL,
                "SUCCESS",
                started,
                utcnow(),
                {
                    "model_id": model.model_id,
                    "tool_calls": [c.name for c in turn.tool_calls],
                    "text_preview": (turn.text or "")[:300],
                    "input_tokens": turn.usage.input_tokens,
                    "output_tokens": turn.usage.output_tokens,
                },
            )
        )
        messages = [*state["messages"], turn.assistant_message()]
        update: dict[str, Any] = {
            "messages": messages,
            "step_count": state["step_count"] + 1,
            "input_tokens": state["input_tokens"] + turn.usage.input_tokens,
            "output_tokens": state["output_tokens"] + turn.usage.output_tokens,
        }
        if not turn.tool_calls:
            update["messages"] = [
                *messages,
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Call a read tool or submit_diagnosis."}],
                },
            ]
        return update

    async def run_tools(state: InvestigationState) -> dict[str, Any]:
        last = state["messages"][-1]
        ctx = run_context(state)
        results: list[dict[str, Any]] = []
        evidence_ids = list(state["evidence_ids"])
        for block in last["content"]:
            if block.get("type") != "tool_use" or block["name"] == SUBMIT_DIAGNOSIS:
                continue
            result = await gateway.invoke(ctx, block["id"], block["name"], block["input"])
            if result.evidence_id:
                evidence_ids.append(result.evidence_id)
            results.append(
                _tool_result(block["id"], result.for_model(), is_error=result.evidence_id is None)
            )
        return {
            "messages": [*state["messages"], {"role": "user", "content": results}],
            "evidence_ids": evidence_ids,
            "tool_call_count": ctx.total_calls,
        }

    async def validate(state: InvestigationState) -> dict[str, Any]:
        last = state["messages"][-1]
        uses = [b for b in last["content"] if b.get("type") == "tool_use"]
        call = next(b for b in uses if b["name"] == SUBMIT_DIAGNOSIS)
        started = utcnow()
        error: str | None = None
        report: DiagnosisReport | None = None
        try:
            payload = dict(call["input"]) if isinstance(call["input"], dict) else {}
            payload["incident_id"] = state["incident_id"]
            report = DiagnosisReport.model_validate(payload)
            evidence.resolve(state["run_id"], report.cited_evidence_ids())
            if not report.diagnosis.supporting_evidence_ids and report.proposed_action:
                raise ValueError("a proposed action requires supporting evidence")
        except (ValidationError, ValueError, TypeError) as exc:
            error = (
                f"unresolved evidence ids: {sorted(exc.missing)}"
                if isinstance(exc, UnresolvedEvidenceError)
                else f"invalid diagnosis: {type(exc).__name__}"
            )
        trajectory.record(
            StepRecord(
                state["run_id"],
                StepKind.DIAGNOSIS,
                "SUCCESS" if error is None else "INVALID",
                started,
                utcnow(),
                {
                    "error": error,
                    "root_cause_id": report.diagnosis.root_cause_id if report else None,
                },
            )
        )
        if error is None and report is not None:
            return {"status": "COMPLETED", "report": report.model_dump(mode="json")}
        if state["repair_attempts"] >= budgets.max_repair_attempts:
            return {"status": "FAILED", "error_code": "invalid_diagnosis"}
        # Every tool_use block must be answered; read calls made in the same turn
        # as the submission are skipped rather than executed.
        feedback = [
            _tool_result(call["id"], {"status": "REJECTED", "reason": error}, True),
            *(
                _tool_result(b["id"], {"status": "SKIPPED", "reason": "submitted together"}, True)
                for b in uses
                if b["id"] != call["id"]
            ),
        ]
        return {
            "messages": [*state["messages"], {"role": "user", "content": feedback}],
            "repair_attempts": state["repair_attempts"] + 1,
        }

    def after_agent(state: InvestigationState) -> str:
        if state["status"] != "RUNNING":
            return END
        last = state["messages"][-1]
        if last["role"] != "assistant":
            return "agent"  # nudge message appended; ask again
        names = [b["name"] for b in last["content"] if b.get("type") == "tool_use"]
        return "validate" if SUBMIT_DIAGNOSIS in names else "tools"

    def after_validate(state: InvestigationState) -> str:
        return END if state["status"] != "RUNNING" else "agent"

    graph = StateGraph(InvestigationState)
    graph.add_node("agent", agent)
    graph.add_node("tools", run_tools)
    graph.add_node("validate", validate)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", after_agent, ["agent", "tools", "validate", END])
    graph.add_edge("tools", "agent")
    graph.add_conditional_edges("validate", after_validate, ["agent", END])
    return graph.compile(checkpointer=checkpointer)
