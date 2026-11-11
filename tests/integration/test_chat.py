"""Shared read-only incident chat: API queues, the agent worker answers."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from incidentops.agent.baseline import RulesBaselineAdapter
from incidentops.agent.model import ModelTurn, ScriptedModelAdapter, ToolCall
from incidentops.api.app import create_app
from incidentops.auth.service import HumanRole
from incidentops.chat.answerer import ANSWER_TOOL, RULES_AUTHOR, ChatAnswerer
from incidentops.chat.service import ANSWER_CHAT_JOB
from incidentops.config import Settings
from incidentops.persistence.models import EvidenceRow, IncidentChatMessageRow, OutboxJobRow
from incidentops.workers.investigation import chat_model, process_chat_one
from tests.integration.auth_helpers import sign_in
from tests.integration.helpers import diagnosed_incident
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


async def test_question_is_queued_and_answered_with_real_citations(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id, _ = await diagnosed_incident(session_factory)
    client = _client(session_factory)
    sign_in(client, session_factory, HumanRole.VIEWER)
    question = _ask(client, incident_id, "Why do you think the consumer was paused?")
    with session_factory() as session:
        job = session.scalars(
            select(OutboxJobRow).where(OutboxJobRow.kind == ANSWER_CHAT_JOB)
        ).one()
        assert job.payload["message_id"] == question["id"]
        evidence_id = session.scalars(select(EvidenceRow.id)).first()
    assert client.get(f"/api/v1/incidents/{incident_id}/chat").json()["pending"] is True

    def answer(messages: list[dict[str, Any]]) -> ModelTurn:
        text = messages[0]["content"][0]["text"]
        assert "<untrusted_incident_data>" in text
        assert evidence_id in text
        return ModelTurn(
            tool_calls=[
                ToolCall(
                    "a1",
                    ANSWER_TOOL,
                    {
                        "answer": f"The status check {evidence_id} showed paused=true.",
                        "cited_evidence_ids": [evidence_id, "ev_" + "0" * 32],
                        "suggest_reinvestigation": False,
                    },
                )
            ]
        )

    model = ScriptedModelAdapter([answer])
    assert await process_chat_one(session_factory, ChatAnswerer(session_factory, model), "w", 30)
    assert model.tool_names == [[ANSWER_TOOL]]

    thread = client.get(f"/api/v1/incidents/{incident_id}/chat").json()
    assert thread["pending"] is False
    reply = thread["messages"][-1]
    assert reply["role"] == "assistant"
    assert reply["reply_to"] == question["id"]
    assert reply["cited_evidence_ids"] == [evidence_id]
    assert reply["records_checked"] > 1
    assert reply["author"] == "scripted-test-model"

    # A redelivered job never writes a second answer.
    assert await ChatAnswerer(session_factory, model).answer(question["id"]) is None


async def test_rules_mode_answers_without_a_model(session_factory: sessionmaker[Session]) -> None:
    incident_id, _ = await diagnosed_incident(session_factory)
    client = _client(session_factory)
    sign_in(client, session_factory)
    _ask(client, incident_id, "What happened?")
    assert chat_model(RulesBaselineAdapter()) is None
    await process_chat_one(session_factory, ChatAnswerer(session_factory, None), "w", 30)
    with session_factory() as session:
        reply = session.scalars(
            select(IncidentChatMessageRow).where(IncidentChatMessageRow.role == "assistant")
        ).one()
    assert reply.author == RULES_AUTHOR
    assert "consumer_paused" in reply.content


async def test_model_failure_still_answers(session_factory: sessionmaker[Session]) -> None:
    incident_id, _ = await diagnosed_incident(session_factory)
    client = _client(session_factory)
    sign_in(client, session_factory)
    _ask(client, incident_id, "Is it fixed?")
    await process_chat_one(
        session_factory, ChatAnswerer(session_factory, ScriptedModelAdapter([])), "w", 30
    )
    reply = client.get(f"/api/v1/incidents/{incident_id}/chat").json()["messages"][-1]
    assert reply["content"].startswith("The model could not answer")


def test_chat_requires_sign_in_and_known_incident(session_factory: sessionmaker[Session]) -> None:
    client = _client(session_factory)
    assert client.get("/api/v1/incidents/inc_x/chat").status_code == 401
    sign_in(client, session_factory)
    missing = client.post("/api/v1/incidents/inc_x/chat", json={"question": "hi"})
    assert missing.status_code == 404
    assert client.post("/api/v1/incidents/inc_x/chat", json={"question": ""}).status_code == 422
