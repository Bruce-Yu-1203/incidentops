"""Proposal evaluation: policy, immutable envelope and approval request."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from incidentops.actions.proposals import ProposalEvaluator
from incidentops.domain.actions import ActionEnvelope
from incidentops.domain.catalog import load_catalog
from incidentops.persistence.models import (
    ActionProposalRow,
    ApprovalRequestRow,
    AuditEventRow,
    IncidentRow,
)
from incidentops.policy.engine import Decision, PolicyEngine, load_policy
from tests.integration.helpers import FakeInspector, diagnosed_incident, production_proposal

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
    assert (policy_audit.decision, policy_audit.policy_version) == ("REQUIRES_APPROVAL", "p1")


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
