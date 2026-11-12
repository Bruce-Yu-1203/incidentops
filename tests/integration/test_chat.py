"""Incident chat agent and human-requested re-investigation (ADR-023)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from incidentops.agent.baseline import RulesBaselineAdapter
from incidentops.agent.model import ModelTurn, ScriptedModelAdapter, ToolCall, evidence_ids_in
from incidentops.api.app import create_app
from incidentops.auth.service import HumanRole
from incidentops.chat.agent import GET_EVIDENCE, ChatAgent, ChatBudgets
from incidentops.chat.answerer import (
    ANSWER_TOOL,
    RULES_AUTHOR,
    ChatAnswerer,
    load_context,
)
from incidentops.chat.service import ANSWER_CHAT_JOB, REINVESTIGATE_JOB
from incidentops.config import Settings
from incidentops.domain.catalog import load_catalog
from incidentops.domain.ids import new_id
from incidentops.evidence.store import DatabaseEvidenceSink
from incidentops.persistence.models import (
    DiagnosisRow,
    EvidenceRow,
    IncidentChatMessageRow,
    IncidentChatSummaryRow,
    IncidentRow,
    InvestigationRunRow,
    OutboxJobRow,
)
from incidentops.tools.gateway import ToolGateway
from incidentops.trajectory.recorder import DatabaseTrajectorySink
from incidentops.workers.chat import chat_model, process_chat_one
from incidentops.workers.investigation import process_reinvestigation_one
from tests.integration.auth_helpers import sign_in
from tests.integration.helpers import diagnosed_incident
from tests.integration.test_investigation_worker import _runner
from tests.unit.test_investigation_graph import CONSUMER, lag_and_status, submit
from tests.unit.tool_fakes import FakeBackend

pytestmark = pytest.mark.integration


def _client(factory: sessionmaker[Session]) -> TestClient:
    return TestClient(
        create_app(Settings(_env_file=None), session_factory=factory, telemetry=FakeBackend())
    )


def _ask(client: TestClient, incident_id: str, question: str) -> dict[str, Any]:
    response = client.post(f"/api/v1/incidents/{incident_id}/chat", json={"question": question})
    assert response.status_code == 202, response.text
    return dict(response.json())


def _agent(
    factory: sessionmaker[Session], model: ScriptedModelAdapter, budgets: ChatBudgets | None = None
) -> ChatAgent:
    trajectory = DatabaseTrajectorySink(factory)
    gateway = ToolGateway(load_catalog(), FakeBackend(), DatabaseEvidenceSink(factory), trajectory)
    return ChatAgent(model, gateway, trajectory, budgets or ChatBudgets())


def _answer(text: str, cited: list[str], suggest: bool = False, reason: str = "") -> ModelTurn:
    arguments: dict[str, Any] = {
        "answer": text,
        "cited_evidence_ids": cited,
        "suggest_reinvestigation": suggest,
    }
    if reason:
        arguments["suggestion_reason"] = reason
    return ModelTurn(tool_calls=[ToolCall("final", ANSWER_TOOL, arguments)])


def _escalate(factory: sessionmaker[Session], incident_id: str) -> None:
    with factory.begin() as session:
        session.execute(
            update(IncidentRow).where(IncidentRow.id == incident_id).values(state="ESCALATED")
        )


async def test_chat_agent_checks_live_data_and_records_its_trajectory(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id, diagnosis_id = await diagnosed_incident(session_factory)
    client = _client(session_factory)
    sign_in(client, session_factory, HumanRole.VIEWER)
    denied = client.post(f"/api/v1/incidents/{incident_id}/chat", json={"question": "hi"})
    assert denied.status_code == 403
    sign_in(client, session_factory, HumanRole.OPERATOR)
    question = _ask(client, incident_id, "Is the consumer reading again?")
    with session_factory() as session:
        recorded = session.scalars(select(EvidenceRow.id)).first()
        state_before = session.get_one(IncidentRow, incident_id).state
    assert recorded is not None

    def check(messages: list[dict[str, Any]]) -> ModelTurn:
        text = messages[0]["content"][0]["text"]
        assert "<untrusted_incident_data>" in text
        assert '"evidence_index"' in text
        assert recorded in text
        return ModelTurn(
            tool_calls=[
                ToolCall("c1", "get_kafka_consumer_lag", {"consumer_group_id": CONSUMER}),
                ToolCall("c2", GET_EVIDENCE, {"evidence_id": recorded}),
            ]
        )

    def reply(messages: list[dict[str, Any]]) -> ModelTurn:
        fresh = evidence_ids_in(messages)
        assert len(fresh) == 1
        expanded = json.loads(messages[-1]["content"][1]["content"])
        assert expanded["untrusted_data"]["id"] == recorded
        return _answer(f"Lag is still high ({fresh[0]}).", [fresh[0], "ev_" + "0" * 32])

    model = ScriptedModelAdapter([check, reply])
    answerer = ChatAnswerer(session_factory, _agent(session_factory, model))
    assert await process_chat_one(session_factory, answerer, "w", 30)
    assert GET_EVIDENCE in model.tool_names[0]
    assert "submit_diagnosis" not in model.tool_names[0]

    thread = client.get(f"/api/v1/incidents/{incident_id}/chat").json()
    assert thread["pending"] is False
    assert thread["running"] is None
    answer = thread["messages"][-1]
    assert answer["reply_to"] == question["id"]
    assert answer["diagnosis_id"] == diagnosis_id
    assert [s["tool"] for s in answer["steps"]] == ["get_kafka_consumer_lag", GET_EVIDENCE]
    assert len(answer["new_evidence_ids"]) == 1
    assert answer["cited_evidence_ids"] == answer["new_evidence_ids"]

    with session_factory() as session:
        run = session.get_one(InvestigationRunRow, answer["run_id"])
        assert (run.kind, run.state, run.tool_call_count) == ("chat", "COMPLETED", 1)
        assert session.get_one(IncidentRow, incident_id).state == state_before
        assert session.get_one(EvidenceRow, answer["new_evidence_ids"][0]).run_id == run.id

    detail = client.get(f"/api/v1/incidents/{incident_id}").json()
    chat_events = [e["event_type"] for e in detail["timeline"] if e["kind"] == "chat"]
    assert chat_events == ["chat_question", "chat_answer", "chat_evidence_added"]
    assert not any((e["event_type"] or "").startswith("chat_turn_") for e in detail["timeline"])
    tool_refs = {e["reference_id"] for e in detail["timeline"] if e["kind"] == "tool"}
    assert answer["new_evidence_ids"][0] not in tool_refs
    assert {r["kind"] for r in detail["runs"]} == {"investigation", "chat"}

    # A redelivered job never writes a second answer.
    assert await answerer.answer(question["id"]) is None


async def test_spent_budget_forces_an_answer(session_factory: sessionmaker[Session]) -> None:
    incident_id, _ = await diagnosed_incident(session_factory)
    client = _client(session_factory)
    sign_in(client, session_factory, HumanRole.OPERATOR)
    _ask(client, incident_id, "Check everything")
    lag = ModelTurn(
        tool_calls=[ToolCall("x", "get_kafka_consumer_lag", {"consumer_group_id": CONSUMER})]
    )
    model = ScriptedModelAdapter([lag, lag, _answer("Here is what I have.", [])])
    agent = _agent(session_factory, model, ChatBudgets(max_tool_calls=2))
    await process_chat_one(session_factory, ChatAnswerer(session_factory, agent), "w", 30)
    assert model.tool_names[-1] == [ANSWER_TOOL]
    with session_factory() as session:
        run = session.scalars(
            select(InvestigationRunRow).where(InvestigationRunRow.kind == "chat")
        ).one()
        assert (run.state, run.tool_call_count) == ("BUDGET_EXCEEDED", 2)


async def test_rules_mode_and_model_failure_still_answer(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id, _ = await diagnosed_incident(session_factory)
    client = _client(session_factory)
    sign_in(client, session_factory, HumanRole.OPERATOR)
    _ask(client, incident_id, "What happened?")
    assert chat_model(RulesBaselineAdapter()) is None
    await process_chat_one(session_factory, ChatAnswerer(session_factory, None), "w", 30)
    _ask(client, incident_id, "Is it fixed?")
    broken = _agent(session_factory, ScriptedModelAdapter([]))
    await process_chat_one(session_factory, ChatAnswerer(session_factory, broken), "w", 30)
    messages = client.get(f"/api/v1/incidents/{incident_id}/chat").json()["messages"]
    assert messages[1]["author"] == RULES_AUTHOR
    assert "consumer_paused" in messages[1]["content"]
    assert messages[3]["content"].startswith("The model could not answer")
    with session_factory() as session:
        states = session.scalars(
            select(InvestigationRunRow.state)
            .where(InvestigationRunRow.kind == "chat")
            .order_by(InvestigationRunRow.created_at)
        ).all()
    assert states == ["COMPLETED", "FAILED"]


async def test_one_turn_per_incident_at_a_time(session_factory: sessionmaker[Session]) -> None:
    incident_id, _ = await diagnosed_incident(session_factory)
    client = _client(session_factory)
    sign_in(client, session_factory, HumanRole.OPERATOR)
    _ask(client, incident_id, "First")
    with session_factory.begin() as session:
        run_id = new_id("run")
        session.add(
            InvestigationRunRow(
                id=run_id,
                incident_id=incident_id,
                incident_version=1,
                checkpoint_thread_id=f"{run_id}:busy",
                state="RUNNING",
                version=1,
                kind="chat",
                budgets={},
            )
        )
    assert await process_chat_one(session_factory, ChatAnswerer(session_factory, None), "w", 30)
    with session_factory() as session:
        job = session.scalars(
            select(OutboxJobRow).where(OutboxJobRow.kind == ANSWER_CHAT_JOB)
        ).one()
        assert (job.status, job.attempts) == ("PENDING", 0)
        assert (
            session.scalar(
                select(func.count())
                .select_from(IncidentChatMessageRow)
                .where(IncidentChatMessageRow.role == "assistant")
            )
            == 0
        )
    running = client.get(f"/api/v1/incidents/{incident_id}/chat").json()["running"]
    assert running["run_id"] == run_id


async def test_older_turns_fold_into_a_rolling_summary(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id, _ = await diagnosed_incident(session_factory)
    client = _client(session_factory)
    sign_in(client, session_factory, HumanRole.OPERATOR)
    for n in range(7):
        question = _ask(client, incident_id, f"question {n}")
        await process_chat_one(session_factory, ChatAnswerer(session_factory, None), "w", 30)
        assert question["role"] == "user"
    with session_factory() as session:
        summary = session.scalars(select(IncidentChatSummaryRow)).one()
        assert summary.covered_messages == 8
        assert "question 0" in summary.content
        context = load_context(session, incident_id, "msg_none")
    assert context.summary == summary.content
    assert [h["content"] for h in context.history if h["role"] == "user"] == [
        "question 4",
        "question 5",
        "question 6",
    ]


async def test_reinvestigation_records_a_new_diagnosis_without_moving_the_incident(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id, first_diagnosis = await diagnosed_incident(session_factory)
    client = _client(session_factory)
    sign_in(client, session_factory, HumanRole.OPERATOR)
    not_yet = client.post(f"/api/v1/incidents/{incident_id}/reinvestigate", json={})
    assert not_yet.status_code == 409
    _escalate(session_factory, incident_id)
    assert client.get(f"/api/v1/incidents/{incident_id}/chat").json()["can_reinvestigate"]

    queued = client.post(
        f"/api/v1/incidents/{incident_id}/reinvestigate",
        json={"reason": "The logs show nobody paused it"},
    )
    assert queued.status_code == 202, queued.text
    assert queued.json()["state"] == "QUEUED"
    again = client.post(f"/api/v1/incidents/{incident_id}/reinvestigate", json={})
    assert again.status_code == 409
    sign_in(client, session_factory, HumanRole.VIEWER)
    assert client.post(f"/api/v1/incidents/{incident_id}/reinvestigate", json={}).status_code == 403

    model = ScriptedModelAdapter([lag_and_status(), submit])
    runner = _runner(session_factory, model)
    assert await process_reinvestigation_one(session_factory, runner, "w", 60)
    first_message = model.calls[0][0]["content"]
    prior = first_message[-1]["text"]
    assert "<untrusted_prior_findings>" in prior
    assert "The logs show nobody paused it" in prior
    assert first_diagnosis not in prior

    with session_factory() as session:
        incident = session.get_one(IncidentRow, incident_id)
        assert incident.state == "ESCALATED"
        run = session.scalars(
            select(InvestigationRunRow).where(InvestigationRunRow.kind == "reinvestigation")
        ).one()
        assert run.state == "COMPLETED"
        assert run.requested_by
        assert run.requested_by.startswith("user:")
        new = session.scalars(select(DiagnosisRow).where(DiagnosisRow.run_id == run.id)).one()
        proposals = session.scalars(
            select(OutboxJobRow).where(OutboxJobRow.kind == "evaluate_action_proposal")
        ).all()
        assert all(job.payload["diagnosis_id"] != new.id for job in proposals)
        assert (
            session.scalars(
                select(OutboxJobRow.status).where(OutboxJobRow.kind == REINVESTIGATE_JOB)
            ).one()
            == "DONE"
        )

    thread = client.get(f"/api/v1/incidents/{incident_id}/chat").json()
    assert thread["latest_diagnosis_id"] == new.id
    assert thread["reinvestigation"]["state"] == "COMPLETED"
    assert thread["can_reinvestigate"] is True
    detail = client.get(f"/api/v1/incidents/{incident_id}").json()
    events = [e["event_type"] for e in detail["timeline"] if e["kind"] == "audit"]
    assert {
        "reinvestigation_requested",
        "reinvestigation_started",
        "reinvestigation_finished",
    } <= set(events)


def test_chat_requires_sign_in_and_known_incident(session_factory: sessionmaker[Session]) -> None:
    client = _client(session_factory)
    assert client.get("/api/v1/incidents/inc_x/chat").status_code == 401
    sign_in(client, session_factory, HumanRole.OPERATOR)
    missing = client.post("/api/v1/incidents/inc_x/chat", json={"question": "hi"})
    assert missing.status_code == 404
    assert client.post("/api/v1/incidents/inc_x/chat", json={"question": ""}).status_code == 422
    assert client.post("/api/v1/incidents/inc_x/reinvestigate", json={}).status_code == 404


async def test_a_failing_turn_is_closed_and_answered_when_the_job_gives_up(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    import incidentops.chat.answerer as answerer_module
    import incidentops.workers.chat as chat_worker

    incident_id, _ = await diagnosed_incident(session_factory)
    client = _client(session_factory)
    sign_in(client, session_factory, HumanRole.OPERATOR)
    question = _ask(client, incident_id, "What should I check?")

    def broken(*_: Any) -> None:
        raise PermissionError("permission denied for table action_proposals")

    monkeypatch.setattr(answerer_module, "load_context", broken)
    monkeypatch.setattr(chat_worker, "MAX_ATTEMPTS", 1)
    assert await process_chat_one(session_factory, ChatAnswerer(session_factory, None), "w", 30)

    thread = client.get(f"/api/v1/incidents/{incident_id}/chat").json()
    assert thread["pending"] is False
    assert thread["running"] is None
    answer = thread["messages"][-1]
    assert answer["reply_to"] == question["id"]
    assert "PermissionError" in answer["content"]
    with session_factory() as session:
        run = session.scalars(
            select(InvestigationRunRow).where(InvestigationRunRow.kind == "chat")
        ).one()
        assert (run.state, run.error_code) == ("FAILED", "harness_error")
        job = session.scalars(
            select(OutboxJobRow).where(OutboxJobRow.kind == ANSWER_CHAT_JOB)
        ).one()
        assert job.status == "DEAD"
