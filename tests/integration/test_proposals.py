"""Proposal evaluation: policy, immutable envelope and approval request."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from incidentops.actions.proposals import ProposalEvaluator
from incidentops.agent.model import ModelTurn
from incidentops.domain.actions import ActionEnvelope
from incidentops.domain.catalog import load_catalog
from incidentops.persistence.models import (
    ActionProposalRow,
    ApprovalRequestRow,
    AuditEventRow,
    DiagnosisRow,
    IncidentRow,
    InvestigationRunRow,
)
from incidentops.policy.engine import Decision, PolicyEngine, load_policy
from tests.integration.helpers import FakeInspector, diagnosed_incident, production_proposal
from tests.unit.test_investigation_graph import submit

pytestmark = pytest.mark.integration


def _evaluator(factory: sessionmaker[Session], inspector: FakeInspector) -> ProposalEvaluator:
    return ProposalEvaluator(factory, PolicyEngine(load_policy(), load_catalog()), inspector)


async def test_valid_proposal_opens_approval_with_frozen_envelope(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id, diagnosis_id = await diagnosed_incident(session_factory)
    outcome = await _evaluator(session_factory, FakeInspector("gen-2")).evaluate(
        incident_id, diagnosis_id
    )
    assert (outcome.decision, outcome.reason) == (Decision.REQUIRES_APPROVAL, "approval_requested")
    with session_factory() as session:
        action = session.get_one(ActionProposalRow, outcome.action_id)
        approval = session.scalars(select(ApprovalRequestRow)).one()
        incident = session.get_one(IncidentRow, incident_id)
        policy_audit = session.scalars(
            select(AuditEventRow).where(AuditEventRow.event_type == "policy_evaluated")
        ).one()
    # The stored envelope reproduces the stored hash exactly.
    envelope = ActionEnvelope.model_validate(
        {k: v for k, v in action.envelope.items() if k != "action_hash"}
    )
    assert envelope.action_hash == action.action_hash == approval.action_hash
    assert envelope.target_revision == "gen-2"
    assert envelope.requested_by.startswith("run:run_")
    assert (action.state, approval.state, incident.state) == (
        "PENDING_APPROVAL",
        "PENDING",
        "AWAITING_APPROVAL",
    )
    assert (policy_audit.decision, policy_audit.policy_version) == ("REQUIRES_APPROVAL", "p3")


async def test_production_proposal_is_denied(session_factory: sessionmaker[Session]) -> None:
    # S-01: the agent proposes a production action; policy denies, nothing is queued.
    incident_id, diagnosis_id = await diagnosed_incident(session_factory, production_proposal)
    outcome = await _evaluator(session_factory, FakeInspector()).evaluate(incident_id, diagnosis_id)
    assert (outcome.decision, outcome.reason) == (Decision.DENY, "policy_denied")
    with session_factory() as session:
        action = session.get_one(ActionProposalRow, outcome.action_id)
        incident = session.get_one(IncidentRow, incident_id)
        assert session.scalars(select(ApprovalRequestRow)).all() == []
    assert (action.state, action.reason_code) == ("POLICY_DENIED", "production_disabled")
    assert (incident.state, incident.escalation_reason) == (
        "DIAGNOSED_ONLY",
        "policy_production_disabled",
    )


def mismatched_proposal(messages: list[dict[str, Any]]) -> ModelTurn:
    # A valid, allowlisted action that does not remediate the diagnosed cause.
    turn = submit(messages)
    turn.tool_calls[0].arguments["diagnosis"]["root_cause_id"] = "kafka_broker_unavailable"
    return turn


def no_action_root_cause_proposal(messages: list[dict[str, Any]]) -> ModelTurn:
    turn = submit(messages)
    turn.tool_calls[0].arguments["diagnosis"]["root_cause_id"] = "insufficient_evidence"
    return turn


@pytest.mark.parametrize("final", [mismatched_proposal, no_action_root_cause_proposal])
async def test_action_inconsistent_with_root_cause_is_denied(
    session_factory: sessionmaker[Session], final: Any
) -> None:
    incident_id, diagnosis_id = await diagnosed_incident(session_factory, final)
    outcome = await _evaluator(session_factory, FakeInspector("gen-2")).evaluate(
        incident_id, diagnosis_id
    )
    assert (outcome.decision, outcome.reason) == (Decision.DENY, "policy_denied")
    with session_factory() as session:
        action = session.get_one(ActionProposalRow, outcome.action_id)
        incident = session.get_one(IncidentRow, incident_id)
        assert session.scalars(select(ApprovalRequestRow)).all() == []
    assert (action.state, action.reason_code) == (
        "POLICY_DENIED",
        "action_not_allowed_for_root_cause",
    )
    # Denied proposals hand the incident to a human.
    assert (incident.state, incident.escalation_reason) == (
        "DIAGNOSED_ONLY",
        "policy_action_not_allowed_for_root_cause",
    )


async def test_unreadable_target_revision_is_denied(
    session_factory: sessionmaker[Session],
) -> None:
    incident_id, diagnosis_id = await diagnosed_incident(session_factory)
    outcome = await _evaluator(session_factory, FakeInspector(revision=None)).evaluate(
        incident_id, diagnosis_id
    )
    assert outcome.decision is Decision.DENY
    with session_factory() as session:
        action = session.get_one(ActionProposalRow, outcome.action_id)
    assert action.reason_code == "missing_target_revision"


async def test_evaluation_is_idempotent(session_factory: sessionmaker[Session]) -> None:
    incident_id, diagnosis_id = await diagnosed_incident(session_factory)
    evaluator = _evaluator(session_factory, FakeInspector())
    first = await evaluator.evaluate(incident_id, diagnosis_id)
    second = await evaluator.evaluate(incident_id, diagnosis_id)
    assert second.action_id == first.action_id
    assert second.reason == "already_evaluated"
    with session_factory() as session:
        assert len(session.scalars(select(ActionProposalRow)).all()) == 1


async def test_second_action_on_same_target_is_denied_while_first_is_active(
    session_factory: sessionmaker[Session],
) -> None:
    first_incident, first_diagnosis = await diagnosed_incident(session_factory)
    evaluator = _evaluator(session_factory, FakeInspector("gen-2"))
    first = await evaluator.evaluate(first_incident, first_diagnosis)
    assert first.decision is Decision.REQUIRES_APPROVAL

    # A second incident (another service's alert) proposes the same target.
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from incidentops.agent.model import ScriptedModelAdapter
    from incidentops.api.app import create_app
    from incidentops.config import Settings
    from incidentops.workers.investigation import process_one
    from tests.integration.test_investigation_worker import OWNER, _runner
    from tests.unit.test_alertmanager_normalize import payload
    from tests.unit.test_investigation_graph import lag_and_status, submit

    client = TestClient(
        create_app(
            Settings(alertmanager_token=SecretStr("alertmanager-token-0123456789")),
            session_factory=session_factory,
        )
    )
    other = payload(fingerprint="abcdefabcdef0001")
    other["alerts"][0]["labels"]["service_id"] = "demo-producer"
    response = client.post(
        "/api/v1/alerts/alertmanager",
        json=other,
        headers={"Authorization": "Bearer alertmanager-token-0123456789"},
    )
    (second_incident,) = response.json()["created_incident_ids"]
    runner = _runner(session_factory, ScriptedModelAdapter([lag_and_status(), submit]))
    await process_one(session_factory, runner, OWNER, 60)
    with session_factory() as session:
        diagnosis_id = session.scalars(
            select(DiagnosisRow.id)
            .join(InvestigationRunRow, InvestigationRunRow.id == DiagnosisRow.run_id)
            .where(InvestigationRunRow.incident_id == second_incident)
        ).one()
    second = await evaluator.evaluate(second_incident, diagnosis_id)
    assert second.decision is Decision.DENY
    with session_factory() as session:
        assert session.get_one(ActionProposalRow, second.action_id).reason_code == (
            "duplicate_active_action"
        )
