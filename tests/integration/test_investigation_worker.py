"""Investigation worker end-to-end on real PostgreSQL with a scripted model."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from incidentops.agent.graph import Budgets
from incidentops.agent.model import ModelTurn, ScriptedModelAdapter, ToolCall, evidence_ids_in
from incidentops.agent.run_manager import EVALUATE_PROPOSAL_JOB, InvestigationRunner
from incidentops.api.app import create_app
from incidentops.config import Settings
from incidentops.domain.catalog import load_catalog
from incidentops.domain.state_machines import IncidentState
from incidentops.evidence.store import DatabaseEvidenceSink
from incidentops.ingestion.service import INVESTIGATE_JOB
from incidentops.persistence import outbox
from incidentops.persistence.models import (
    AgentStepRow,
    AuditEventRow,
    DiagnosisRow,
    EvidenceRow,
    IncidentRow,
    InvestigationRunRow,
    OutboxJobRow,
)
from incidentops.persistence.transitions import AuditSpec, transition
from incidentops.tools.gateway import ToolGateway
from incidentops.trajectory.recorder import DatabaseTrajectorySink
from incidentops.workers.investigation import process_one
from tests.unit.test_alertmanager_normalize import payload
from tests.unit.test_investigation_graph import CONSUMER, lag_and_status, submit
from tests.unit.tool_fakes import FakeBackend

pytestmark = pytest.mark.integration

TOKEN = "alertmanager-token-0123456789"
OWNER = "worker-test"


def _ingest(factory: sessionmaker[Session]) -> str:
    settings = Settings(_env_file=None, alertmanager_token=SecretStr(TOKEN))
    client = TestClient(create_app(settings, session_factory=factory))
    response = client.post(
        "/api/v1/alerts/alertmanager", json=payload(), headers={"Authorization": f"Bearer {TOKEN}"}
    )
    return str(response.json()["created_incident_ids"][0])


def _runner(
    factory: sessionmaker[Session], model: ScriptedModelAdapter, budgets: Budgets | None = None
) -> InvestigationRunner:
    evidence = DatabaseEvidenceSink(factory)
    trajectory = DatabaseTrajectorySink(factory)
    catalog = load_catalog()
    gateway = ToolGateway(catalog, FakeBackend(), evidence, trajectory)
    return InvestigationRunner(
        factory, catalog, model, gateway, evidence, trajectory, budgets or Budgets()
    )


def _count(factory: sessionmaker[Session], model: Any, *where: Any) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(model).where(*where)) or 0


def _incident(factory: sessionmaker[Session], incident_id: str) -> IncidentRow:
    with factory() as session:
        return session.get_one(IncidentRow, incident_id)


def abstain(messages: list[dict[str, Any]]) -> ModelTurn:
    return ModelTurn(
        tool_calls=[
            ToolCall(
                "final",
                "submit_diagnosis",
                {
                    "diagnosis": {
                        "root_cause_id": "insufficient_evidence",
                        "affected_component": CONSUMER,
                        "confidence_level": "low",
                        "summary": "Signals conflict.",
                        "supporting_evidence_ids": evidence_ids_in(messages),
                    },
                    "needs_human_review": True,
                },
            )
        ]
    )


def diagnose_without_action(messages: list[dict[str, Any]]) -> ModelTurn:
    turn = submit(messages)
    turn.tool_calls[0].arguments.pop("proposed_action")
    turn.tool_calls[0].arguments["needs_human_review"] = True
    return turn


async def test_full_investigation_persists_diagnosis_evidence_and_trajectory(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = _ingest(session_factory)
    runner = _runner(session_factory, ScriptedModelAdapter([lag_and_status(), submit]))
    assert await process_one(session_factory, runner, OWNER, 60)

    incident = _incident(session_factory, incident_id)
    assert incident.state == IncidentState.DIAGNOSED.value
    with session_factory() as session:
        run = session.scalars(select(InvestigationRunRow)).one()
        assert (run.state, run.model_id, run.prompt_version) == (
            "COMPLETED",
            "scripted-test-model",
            "investigation-v1",
        )
        assert (run.step_count, run.tool_call_count) == (2, 2)
        diagnosis = session.scalars(select(DiagnosisRow)).one()
        assert diagnosis.root_cause_id == "consumer_paused"
        evidence_ids = set(session.scalars(select(EvidenceRow.id)).all())
        assert set(diagnosis.cited_evidence_ids) == evidence_ids
        jobs = {j.kind: j.status for j in session.scalars(select(OutboxJobRow))}
        assert jobs == {INVESTIGATE_JOB: "DONE", EVALUATE_PROPOSAL_JOB: "PENDING"}
        events = [
            e.event_type for e in session.scalars(select(AuditEventRow).order_by(AuditEventRow.seq))
        ]
    assert events[1:] == [
        "incident_triaged",
        "investigation_started",
        "run_started",
        "run_finished",
        "incident_diagnosed",
    ]
    assert _count(session_factory, AgentStepRow) == 5


@pytest.mark.parametrize(
    ("final_step", "expected_state", "reason"),
    [
        (abstain, IncidentState.ESCALATED, "insufficient_evidence"),
        (diagnose_without_action, IncidentState.DIAGNOSED_ONLY, None),
    ],
)
async def test_post_diagnosis_routing(
    session_factory: sessionmaker[Session],
    final_step: Callable[[list[dict[str, Any]]], ModelTurn],
    expected_state: IncidentState,
    reason: str | None,
) -> None:
    incident_id = _ingest(session_factory)
    runner = _runner(session_factory, ScriptedModelAdapter([lag_and_status(), final_step]))
    await process_one(session_factory, runner, OWNER, 60)
    incident = _incident(session_factory, incident_id)
    assert (incident.state, incident.escalation_reason) == (expected_state.value, reason)
    assert _count(session_factory, OutboxJobRow, OutboxJobRow.kind == EVALUATE_PROPOSAL_JOB) == 0


async def test_model_failure_fails_run_and_incident(session_factory: sessionmaker[Session]) -> None:
    incident_id = _ingest(session_factory)
    await process_one(
        session_factory, _runner(session_factory, ScriptedModelAdapter([])), OWNER, 60
    )
    incident = _incident(session_factory, incident_id)
    assert (incident.state, incident.escalation_reason) == ("FAILED", "model_error")
    assert _count(session_factory, DiagnosisRow) == 0
    assert _count(session_factory, OutboxJobRow, OutboxJobRow.status == "DONE") == 1


async def test_budget_exhaustion_reports_partial_findings(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = _ingest(session_factory)
    model = ScriptedModelAdapter([lag_and_status()] * 5)
    await process_one(
        session_factory, _runner(session_factory, model, Budgets(max_steps=2)), OWNER, 60
    )
    incident = _incident(session_factory, incident_id)
    assert (incident.state, incident.escalation_reason) == ("ESCALATED", "insufficient_evidence")
    with session_factory() as session:
        run = session.scalars(select(InvestigationRunRow)).one()
        diagnosis = session.scalars(select(DiagnosisRow)).one()
    assert run.state == "BUDGET_EXCEEDED"
    assert diagnosis.root_cause_id == "insufficient_evidence"
    assert diagnosis.cited_evidence_ids  # partial evidence is preserved


async def test_no_job_returns_false(session_factory: sessionmaker[Session]) -> None:
    runner = _runner(session_factory, ScriptedModelAdapter([]))
    assert not await process_one(session_factory, runner, OWNER, 60)


def test_concurrent_claims_never_share_a_job(session_factory: sessionmaker[Session]) -> None:
    _ingest(session_factory)
    first = session_factory()
    second = session_factory()
    try:
        with first.begin():
            job_a = outbox.claim(first, INVESTIGATE_JOB, "a", 60)
            with second.begin():
                job_b = outbox.claim(second, INVESTIGATE_JOB, "b", 60)
        assert job_a is not None
        assert job_b is None
    finally:
        first.close()
        second.close()


async def test_expired_lease_is_reclaimed_and_stale_run_closed(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = _ingest(session_factory)
    # Simulate a worker that claimed the job, started a run and then died.
    crashed = _runner(session_factory, ScriptedModelAdapter([]))
    with session_factory.begin() as session:
        assert outbox.claim(session, INVESTIGATE_JOB, "dead-worker", 60) is not None
    assert crashed._start(incident_id) is not None
    with session_factory.begin() as session:
        session.execute(text("UPDATE outbox_jobs SET lease_until = now() - interval '1 second'"))

    runner = _runner(session_factory, ScriptedModelAdapter([lag_and_status(), submit]))
    assert await process_one(session_factory, runner, OWNER, 60)
    with session_factory() as session:
        runs = {r.state: r.error_code for r in session.scalars(select(InvestigationRunRow))}
    assert runs == {"FAILED": "worker_lost", "COMPLETED": None}
    assert _incident(session_factory, incident_id).state == "DIAGNOSED"


async def test_cancelled_incident_is_not_investigated(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = _ingest(session_factory)
    with session_factory.begin() as session:
        transition(
            session,
            IncidentRow,
            incident_id,
            expected_state=IncidentState.RECEIVED,
            expected_version=1,
            target_state=IncidentState.CANCELLED,
            audit=AuditSpec(actor="test", event_type="cancelled"),
        )
    model = ScriptedModelAdapter([lag_and_status(), submit])
    assert await process_one(session_factory, _runner(session_factory, model), OWNER, 60)
    assert model.calls == []
    assert _count(session_factory, InvestigationRunRow) == 0


async def test_postgres_checkpointer_records_the_run(
    session_factory: sessionmaker[Session], database_url: str
) -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    incident_id = _ingest(session_factory)
    async with AsyncPostgresSaver.from_conn_string(database_url.replace("+psycopg", "")) as saver:
        await saver.setup()
        evidence = DatabaseEvidenceSink(session_factory)
        trajectory = DatabaseTrajectorySink(session_factory)
        catalog = load_catalog()
        runner = InvestigationRunner(
            session_factory,
            catalog,
            ScriptedModelAdapter([lag_and_status(), submit]),
            ToolGateway(catalog, FakeBackend(), evidence, trajectory),
            evidence,
            trajectory,
            Budgets(),
            checkpointer=saver,
        )
        outcome = await runner.run(incident_id)
        with session_factory() as session:
            thread_id = session.get_one(InvestigationRunRow, outcome.run_id).checkpoint_thread_id
        checkpoint = await saver.aget_tuple({"configurable": {"thread_id": thread_id}})
    assert checkpoint is not None
    assert checkpoint.checkpoint["channel_values"]["status"] == "COMPLETED"


async def test_investigation_api_links_incident_run_steps_and_evidence(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id = _ingest(session_factory)
    runner = _runner(session_factory, ScriptedModelAdapter([lag_and_status(), submit]))
    await process_one(session_factory, runner, OWNER, 60)
    client = TestClient(create_app(Settings(_env_file=None), session_factory=session_factory))

    incident = client.get(f"/api/v1/incidents/{incident_id}").json()
    (run,) = incident["runs"]
    assert incident["diagnosis"]["diagnosis"]["root_cause_id"] == "consumer_paused"
    assert [e["kind"] for e in incident["timeline"]].count("tool") == 2

    detail = client.get(f"/api/v1/investigations/{run['id']}").json()
    assert [s["kind"] for s in detail["steps"]] == [
        "model_call",
        "tool_call",
        "tool_call",
        "model_call",
        "diagnosis",
    ]
    by_source = {e["source"]: e for e in detail["evidence"]}
    assert set(by_source) == {"prometheus", "worker_status"}
    lag_link = by_source["prometheus"]["source_url"]
    assert lag_link.startswith("http://127.0.0.1:3000/explore?")
    assert "kafka_consumergroup_lag" in lag_link
    assert by_source["worker_status"]["source_url"] is None
    cited = set(detail["diagnosis"]["diagnosis"]["supporting_evidence_ids"])
    assert cited == {e["id"] for e in detail["evidence"]}
    assert client.get("/api/v1/investigations/run_missing").status_code == 404
