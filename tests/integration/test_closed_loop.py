"""B-F03 control-plane loop through the worker job handlers (fakes for the lab).

alert -> investigation -> proposal evaluation -> human approval -> executor
-> verifier -> RESOLVED, all driven through outbox jobs.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from incidentops.actions.proposals import ProposalEvaluator
from incidentops.api.app import create_app
from incidentops.auth.service import HumanRole, create_user
from incidentops.config import Settings
from incidentops.domain.catalog import load_catalog
from incidentops.execution.adapters import ApplyResult, ApplyStatus, LookupResult, LookupStatus
from incidentops.execution.executor import ActionExecutor
from incidentops.persistence.models import ActionProposalRow, AuditEventRow, IncidentRow
from incidentops.policy.engine import PolicyEngine, load_policy
from incidentops.verification.verifier import RecoveryVerifier, VerifyConfig
from incidentops.workers import control, executor, verifier
from incidentops.workers.common import process_next
from tests.integration.helpers import FakeAdapter, FakeInspector, diagnosed_incident
from tests.integration.test_verifier import SPEC, FakeClock, LagBackend

pytestmark = pytest.mark.integration


async def test_b_f03_closed_loop_resolves_with_one_dispatch(
    session_factory: sessionmaker[Session],
) -> None:
    catalog = load_catalog()
    policy = PolicyEngine(load_policy(), catalog)
    incident_id, _ = await diagnosed_incident(session_factory)

    # Control worker evaluates the proposal.
    evaluator = ProposalEvaluator(session_factory, policy, FakeInspector("gen-2"))
    assert await process_next(session_factory, control.handlers(evaluator), "control", 60)
    with session_factory() as session:
        action = session.scalars(select(ActionProposalRow)).one()
    assert action.state == "PENDING_APPROVAL"

    # A human approves the exact hash over HTTP.
    with session_factory.begin() as session:
        create_user(session, "approver", "lab-password-0123", HumanRole.APPROVER)
    client = TestClient(create_app(Settings(_env_file=None), session_factory=session_factory))
    client.post(
        "/api/v1/auth/login", json={"username": "approver", "password": "lab-password-0123"}
    )
    approved = client.post(
        f"/api/v1/actions/{action.id}/approve",
        json={"action_hash": action.action_hash, "expected_version": action.version},
    )
    assert approved.status_code == 200, approved.text

    # Executor worker dispatches exactly once.
    adapter = FakeAdapter("gen-2")
    exec_handlers = executor.handlers(
        ActionExecutor(session_factory, policy, {"resume_test_consumer": adapter})
    )
    assert await process_next(session_factory, exec_handlers, "executor", 60)
    assert adapter.apply_calls == [action.id]

    # Verifier worker observes the recovery independently.
    clock = FakeClock()
    recovery = RecoveryVerifier(
        session_factory,
        catalog,
        LagBackend([2400, 60, 0, 0, 0, 0]),
        FakeInspector("gen-3", paused=False),
        VerifyConfig(schema_version=1, specs={"resume_test_consumer": SPEC}),
        clock=clock,
        sleep=clock.sleep,
    )
    assert await process_next(session_factory, verifier.handlers(recovery), "verifier", 60)

    with session_factory() as session:
        incident = session.get_one(IncidentRow, incident_id)
        events = [
            e.event_type for e in session.scalars(select(AuditEventRow).order_by(AuditEventRow.seq))
        ]
    assert incident.state == "RESOLVED"
    for expected in (
        "incident_created",
        "incident_diagnosed",
        "policy_evaluated",
        "approval_requested",
        "approval_decided",
        "action_dispatched",
        "action_succeeded",
        "verification_completed",
    ):
        assert expected in events, expected


async def test_reconcile_gives_up_after_max_attempts(
    session_factory: sessionmaker[Session],
) -> None:
    from tests.integration.test_executor import _queued_action

    action_id = await _queued_action(session_factory)
    adapter = FakeAdapter()
    adapter.apply_result = ApplyResult(ApplyStatus.AMBIGUOUS)
    adapter.lookup = LookupResult(LookupStatus.UNKNOWN)
    policy = PolicyEngine(load_policy(), load_catalog())
    handlers = executor.handlers(
        ActionExecutor(session_factory, policy, {"resume_test_consumer": adapter})
    )
    await process_next(session_factory, handlers, "executor", 60)  # execute -> RECONCILING
    await handlers["reconcile_action"]({"action_id": action_id}, executor.RECONCILE_MAX_ATTEMPTS)
    with session_factory() as session:
        assert session.get_one(ActionProposalRow, action_id).state == "ESCALATED"
    assert adapter.apply_calls == [action_id]
