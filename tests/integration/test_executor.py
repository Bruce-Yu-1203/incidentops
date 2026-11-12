"""Executor revalidation, dispatch and reconciliation (S-03/04/09/10, R-03/06/07)."""

from __future__ import annotations

import asyncio
import threading
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from incidentops.actions.approvals import approve, cancel
from incidentops.actions.proposals import ProposalEvaluator
from incidentops.auth.service import AuthenticatedUser, HumanRole
from incidentops.domain.catalog import ServiceCatalog, load_catalog
from incidentops.execution.adapters import ApplyResult, ApplyStatus, LookupResult, LookupStatus
from incidentops.execution.executor import (
    RECONCILE_ACTION_JOB,
    VERIFY_ACTION_JOB,
    ActionExecutor,
    ExecutionOutcome,
    ReconcileRetryError,
)
from incidentops.persistence.models import (
    ActionProposalRow,
    ExternalOperationRow,
    IncidentRow,
    OutboxJobRow,
)
from incidentops.policy.engine import PolicyConfig, PolicyEngine, load_policy
from incidentops.time import utcnow
from tests.integration.helpers import FakeAdapter, FakeInspector, diagnosed_incident

pytestmark = pytest.mark.integration

APPROVER = AuthenticatedUser("usr_x", "approver", HumanRole.APPROVER)
OPERATOR = AuthenticatedUser("usr_y", "operator", HumanRole.OPERATOR)


def _policy() -> PolicyEngine:
    return PolicyEngine(load_policy(), load_catalog())


async def _queued_action(factory: sessionmaker[Session]) -> str:
    incident_id, diagnosis_id = await diagnosed_incident(factory)
    outcome = await ProposalEvaluator(factory, _policy(), FakeInspector("gen-2")).evaluate(
        incident_id, diagnosis_id
    )
    assert outcome.action_id is not None
    with factory.begin() as session:
        action = session.get_one(ActionProposalRow, outcome.action_id)
        result = approve(session, action.id, APPROVER, action.action_hash, action.version, utcnow())
        assert result.ok
    return outcome.action_id


def _executor(
    factory: sessionmaker[Session], adapter: FakeAdapter, policy: PolicyEngine | None = None
) -> ActionExecutor:
    return ActionExecutor(factory, policy or _policy(), {"resume_test_consumer": adapter})


def _state(
    factory: sessionmaker[Session], action_id: str
) -> tuple[str, str | None, str, str | None]:
    with factory() as session:
        action = session.get_one(ActionProposalRow, action_id)
        incident = session.get_one(IncidentRow, action.incident_id)
        return action.state, action.reason_code, incident.state, incident.escalation_reason


def _jobs(factory: sessionmaker[Session], kind: str) -> int:
    with factory() as session:
        return len(session.scalars(select(OutboxJobRow).where(OutboxJobRow.kind == kind)).all())


async def test_successful_dispatch_moves_incident_to_verifying(
    session_factory: sessionmaker[Session],
) -> None:
    action_id = await _queued_action(session_factory)
    adapter = FakeAdapter()
    result = await _executor(session_factory, adapter).execute(action_id)
    assert result.outcome is ExecutionOutcome.SUCCEEDED
    assert adapter.apply_calls == [action_id]
    assert _state(session_factory, action_id)[::2] == ("SUCCEEDED", "VERIFYING")
    assert _jobs(session_factory, VERIFY_ACTION_JOB) == 1
    with session_factory() as session:
        operation = session.scalars(select(ExternalOperationRow)).one()
    assert (operation.status, operation.provider_operation_id) == ("COMPLETED", action_id)
    # Running the job again (at-least-once delivery) does not dispatch twice.
    again = await _executor(session_factory, adapter).execute(action_id)
    assert again.outcome is ExecutionOutcome.SKIPPED
    assert adapter.apply_calls == [action_id]


async def test_expired_approval_is_not_dispatched(session_factory: sessionmaker[Session]) -> None:
    # S-03
    action_id = await _queued_action(session_factory)
    adapter = FakeAdapter()
    executor = ActionExecutor(
        session_factory,
        _policy(),
        {"resume_test_consumer": adapter},
        clock=lambda: utcnow() + timedelta(hours=2),
    )
    result = await executor.execute(action_id)
    assert result.outcome is ExecutionOutcome.EXPIRED
    assert adapter.apply_calls == []
    assert _state(session_factory, action_id) == (
        "EXPIRED",
        "approval_expired",
        "ESCALATED",
        "approval_expired",
    )


async def test_changed_target_revision_blocks_stale_approval(
    session_factory: sessionmaker[Session],
) -> None:
    # S-04 / R-08: the worker restarted (new generation) after approval.
    action_id = await _queued_action(session_factory)
    adapter = FakeAdapter(revision="gen-7")
    result = await _executor(session_factory, adapter).execute(action_id)
    assert (result.outcome, result.reason) == (ExecutionOutcome.FAILED, "stale_target_revision")
    assert adapter.apply_calls == []


async def test_tampered_stored_arguments_are_detected(
    session_factory: sessionmaker[Session],
) -> None:
    # S-04: arguments altered after approval no longer match the approved hash.
    action_id = await _queued_action(session_factory)
    with session_factory.begin() as session:
        action = session.get_one(ActionProposalRow, action_id)
        envelope = dict(action.envelope)
        envelope["arguments"] = {"desired_paused": True}
        session.execute(
            update(ActionProposalRow)
            .where(ActionProposalRow.id == action_id)
            .values(envelope=envelope)
        )
    adapter = FakeAdapter()
    result = await _executor(session_factory, adapter).execute(action_id)
    assert result.reason == "action_hash_mismatch"
    assert adapter.apply_calls == []


async def test_policy_failure_at_execution_fails_closed(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    # S-09: policy evaluation breaks between approval and dispatch.
    action_id = await _queued_action(session_factory)

    def unavailable(*_: Any) -> bool:
        raise ConnectionError("policy store down")

    monkeypatch.setattr(ServiceCatalog, "allows_action", unavailable)
    adapter = FakeAdapter()
    result = await _executor(session_factory, adapter).execute(action_id)
    assert result.reason == "policy_policy_error"
    assert adapter.apply_calls == []


async def test_policy_version_change_blocks_dispatch(
    session_factory: sessionmaker[Session],
) -> None:
    action_id = await _queued_action(session_factory)
    raw = load_policy().model_dump(mode="json")
    raw["policy_version"] = "p99"
    adapter = FakeAdapter()
    executor = _executor(
        session_factory, adapter, PolicyEngine(PolicyConfig.model_validate(raw), load_catalog())
    )
    assert (await executor.execute(action_id)).reason == "policy_version_changed"
    assert adapter.apply_calls == []


async def test_cancelled_before_dispatch_is_skipped(session_factory: sessionmaker[Session]) -> None:
    # S-10
    action_id = await _queued_action(session_factory)
    with session_factory.begin() as session:
        action = session.get_one(ActionProposalRow, action_id)
        assert cancel(session, action_id, OPERATOR, action.version).ok
    adapter = FakeAdapter()
    assert (await _executor(session_factory, adapter).execute(action_id)).outcome is (
        ExecutionOutcome.SKIPPED
    )
    assert adapter.apply_calls == []


async def test_controller_refusal_fails_and_escalates(
    session_factory: sessionmaker[Session],
) -> None:
    action_id = await _queued_action(session_factory)
    adapter = FakeAdapter()
    adapter.apply_result = ApplyResult(ApplyStatus.REJECTED, detail="stale_generation")
    result = await _executor(session_factory, adapter).execute(action_id)
    assert result.outcome is ExecutionOutcome.FAILED
    assert _state(session_factory, action_id)[2:] == ("ESCALATED", "action_failed")


async def test_ambiguous_outcome_is_reconciled_not_replayed(
    session_factory: sessionmaker[Session],
) -> None:
    # R-07: the request timed out; the outcome is looked up by action_id.
    action_id = await _queued_action(session_factory)
    adapter = FakeAdapter()
    adapter.apply_result = ApplyResult(ApplyStatus.AMBIGUOUS, detail="ReadTimeout")
    executor = _executor(session_factory, adapter)
    assert (await executor.execute(action_id)).outcome is ExecutionOutcome.RECONCILING
    assert _jobs(session_factory, RECONCILE_ACTION_JOB) == 1

    adapter.lookup = LookupResult(LookupStatus.UNKNOWN)
    with pytest.raises(ReconcileRetryError):
        await executor.reconcile(action_id)

    adapter.lookup = LookupResult(LookupStatus.FOUND, {"status": "COMPLETED"})
    assert (await executor.reconcile(action_id)).outcome is ExecutionOutcome.SUCCEEDED
    assert adapter.apply_calls == [action_id]
    assert _state(session_factory, action_id)[::2] == ("SUCCEEDED", "VERIFYING")


async def test_lookup_not_found_means_not_applied(session_factory: sessionmaker[Session]) -> None:
    action_id = await _queued_action(session_factory)
    adapter = FakeAdapter()
    adapter.apply_result = ApplyResult(ApplyStatus.AMBIGUOUS)
    adapter.lookup = LookupResult(LookupStatus.NOT_FOUND)
    executor = _executor(session_factory, adapter)
    await executor.execute(action_id)
    result = await executor.reconcile(action_id)
    assert (result.outcome, result.reason) == (ExecutionOutcome.FAILED, "not_applied")


async def test_unresolvable_outcome_escalates(session_factory: sessionmaker[Session]) -> None:
    action_id = await _queued_action(session_factory)
    adapter = FakeAdapter()
    adapter.apply_result = ApplyResult(ApplyStatus.AMBIGUOUS)
    adapter.lookup = LookupResult(LookupStatus.UNKNOWN)
    executor = _executor(session_factory, adapter)
    await executor.execute(action_id)
    result = await executor.reconcile(action_id, give_up=True)
    assert result.outcome is ExecutionOutcome.ESCALATED
    assert _state(session_factory, action_id) == (
        "ESCALATED",
        "outcome_unknown",
        "ESCALATED",
        "outcome_unknown",
    )


async def test_crash_after_remote_accept_reconciles_without_resend(
    session_factory: sessionmaker[Session],
) -> None:
    # R-03: the process dies after the controller accepted but before the receipt.
    action_id = await _queued_action(session_factory)
    adapter = FakeAdapter()
    adapter.crash_on_apply = True
    with pytest.raises(RuntimeError, match="killed"):
        await _executor(session_factory, adapter).execute(action_id)
    assert _state(session_factory, action_id)[0] == "EXECUTING"

    adapter.crash_on_apply = False
    restarted = _executor(session_factory, adapter)
    result = await restarted.execute(action_id)
    assert result.outcome is ExecutionOutcome.SUCCEEDED
    assert adapter.apply_calls == [action_id]  # exactly one dispatch overall


def test_racing_executors_dispatch_once(session_factory: sessionmaker[Session]) -> None:
    # R-06: two executor processes handle the same action concurrently.
    action_id = asyncio.run(_queued_action(session_factory))
    adapter = FakeAdapter()
    barrier = threading.Barrier(2)
    outcomes: list[ExecutionOutcome] = []

    def run() -> None:
        barrier.wait()
        outcomes.append(asyncio.run(_executor(session_factory, adapter).execute(action_id)).outcome)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes) == sorted([ExecutionOutcome.SUCCEEDED, ExecutionOutcome.SKIPPED])
    assert adapter.apply_calls == [action_id]
