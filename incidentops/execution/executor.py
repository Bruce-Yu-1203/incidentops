"""Approved-action executor with pre-dispatch revalidation and reconciliation.

Sequence for one action (architecture §9.5):

1. Lock the action. Only ``QUEUED`` actions are dispatched. Re-verify the
   approval, the stored envelope hash, the TTL, the current policy and the
   target revision. Any failure ends the action without an external call.
2. Commit ``EXECUTING`` plus an ``external_operations`` row *before* the call,
   so a crash afterwards is visibly in flight.
3. Call the adapter once with the stable ``action_id``.
4. Record the receipt: COMPLETED -> SUCCEEDED (incident VERIFYING), REJECTED ->
   FAILED (incident ESCALATED), AMBIGUOUS -> RECONCILING.

An action found in ``EXECUTING`` or ``RECONCILING`` is never re-sent: the
executor looks up the operation by ``action_id`` instead (R-03, R-07).
"""

from __future__ import annotations

import hmac
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from incidentops.domain.actions import ActionEnvelope
from incidentops.domain.ids import new_id
from incidentops.domain.state_machines import ActionState, ApprovalState, IncidentState
from incidentops.execution.adapters import (
    ApplyResult,
    ApplyStatus,
    LookupStatus,
    RemediationAdapter,
)
from incidentops.persistence.models import (
    ActionProposalRow,
    ApprovalRequestRow,
    ExternalOperationRow,
    IncidentRow,
)
from incidentops.persistence.transitions import AuditSpec, OutboxSpec, transition
from incidentops.policy.engine import Decision, PolicyEngine, PolicyRequest
from incidentops.time import utcnow

ACTOR: Final = "service:executor"
VERIFY_ACTION_JOB: Final = "verify_action"
RECONCILE_ACTION_JOB: Final = "reconcile_action"


class ExecutionOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RECONCILING = "RECONCILING"
    ESCALATED = "ESCALATED"
    EXPIRED = "EXPIRED"
    SKIPPED = "SKIPPED"


class ReconcileRetryError(RuntimeError):
    """The operation status is still unknown; retry the reconcile job later."""


@dataclass(frozen=True)
class Result:
    outcome: ExecutionOutcome
    reason: str | None = None


def stored_envelope(action: ActionProposalRow) -> ActionEnvelope:
    data = {k: v for k, v in action.envelope.items() if k != "action_hash"}
    return ActionEnvelope.model_validate(data)


class ActionExecutor:
    def __init__(
        self,
        factory: sessionmaker[Session],
        policy: PolicyEngine,
        adapters: Mapping[str, RemediationAdapter],
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._factory = factory
        self._policy = policy
        self._adapters = adapters
        self._clock = clock

    def _audit(self, event: str, action: ActionProposalRow, reason: str | None = None) -> AuditSpec:
        return AuditSpec(
            actor=ACTOR,
            event_type=event,
            reason=reason,
            action_hash=action.action_hash,
            policy_version=action.policy_version,
            correlation_ids={"incident_id": action.incident_id, "action_id": action.id},
        )

    def _fail_before_dispatch(
        self, session: Session, action: ActionProposalRow, target: ActionState, reason: str
    ) -> Result:
        """End a QUEUED action without any external call and escalate the incident."""
        transition(
            session,
            ActionProposalRow,
            action.id,
            expected_state=ActionState.QUEUED,
            expected_version=action.version,
            target_state=target,
            audit=self._audit("dispatch_refused", action, reason),
            extra_values={"reason_code": reason},
        )
        self._escalate(session, action, reason, {IncidentState.AWAITING_APPROVAL})
        outcome = (
            ExecutionOutcome.EXPIRED if target is ActionState.EXPIRED else ExecutionOutcome.FAILED
        )
        return Result(outcome, reason)

    def _escalate(
        self,
        session: Session,
        action: ActionProposalRow,
        reason: str,
        from_states: set[IncidentState],
    ) -> None:
        incident = session.get_one(IncidentRow, action.incident_id, with_for_update=True)
        if IncidentState(incident.state) in from_states:
            transition(
                session,
                IncidentRow,
                incident.id,
                expected_state=IncidentState(incident.state),
                expected_version=incident.version,
                target_state=IncidentState.ESCALATED,
                audit=self._audit("incident_escalated", action, reason),
                extra_values={"escalation_reason": reason},
            )

    async def execute(self, action_id: str) -> Result:
        with self._factory() as session:
            action = session.get(ActionProposalRow, action_id)
            state = ActionState(action.state) if action else None
        if state in {ActionState.EXECUTING, ActionState.RECONCILING}:
            # A previous attempt may have reached the controller: never resend.
            return await self.reconcile(action_id)
        if state is not ActionState.QUEUED or action is None:
            return Result(ExecutionOutcome.SKIPPED, f"state {state}")

        adapter = self._adapters.get(action.action_type)
        envelope = stored_envelope(action)
        # Read the live target revision before taking row locks.
        live_revision = await adapter.inspect_target(envelope.target_id) if adapter else None

        with self._factory.begin() as session:
            action = session.get_one(ActionProposalRow, action_id, with_for_update=True)
            if action.state != ActionState.QUEUED:
                return Result(ExecutionOutcome.SKIPPED, f"state {action.state}")
            refusal = self._precondition_failure(session, action, envelope, adapter, live_revision)
            if refusal is not None:
                target, reason = refusal
                return self._fail_before_dispatch(session, action, target, reason)
            transition(
                session,
                ActionProposalRow,
                action.id,
                expected_state=ActionState.QUEUED,
                expected_version=action.version,
                target_state=ActionState.EXECUTING,
                audit=self._audit("action_dispatched", action),
            )
            assert adapter is not None  # noqa: S101 - guaranteed by _precondition_failure
            session.add(
                ExternalOperationRow(
                    id=new_id("op"),
                    action_id=action.id,
                    provider=adapter.provider,
                    provider_operation_id=action.id,
                    status="DISPATCHING",
                )
            )
            incident = session.get_one(IncidentRow, action.incident_id, with_for_update=True)
            transition(
                session,
                IncidentRow,
                incident.id,
                expected_state=IncidentState.AWAITING_APPROVAL,
                expected_version=incident.version,
                target_state=IncidentState.EXECUTING,
                audit=self._audit("incident_executing", action),
            )

        result = await adapter.apply(envelope)
        return self._record(action_id, result)

    def _precondition_failure(
        self,
        session: Session,
        action: ActionProposalRow,
        envelope: ActionEnvelope,
        adapter: RemediationAdapter | None,
        live_revision: str | None,
    ) -> tuple[ActionState, str] | None:
        if adapter is None:
            return ActionState.FAILED, "no_adapter_for_action_type"
        # No row lock on the approval: approve/reject/cancel all lock the action
        # row first, which this transaction already holds, so the approval cannot
        # change underneath us. The executor's database role only reads approvals.
        approval = session.scalars(
            select(ApprovalRequestRow)
            .where(ApprovalRequestRow.action_id == action.id)
            .order_by(ApprovalRequestRow.created_at.desc())
        ).first()
        if approval is None or approval.state != ApprovalState.APPROVED:
            return ActionState.FAILED, "approval_not_valid"
        # The stored envelope must still produce the approved hash (S-04).
        if not (
            hmac.compare_digest(envelope.action_hash, action.action_hash)
            and hmac.compare_digest(action.action_hash, approval.action_hash)
        ):
            return ActionState.FAILED, "action_hash_mismatch"
        if envelope.is_expired(self._clock()):
            return ActionState.EXPIRED, "approval_expired"  # S-03
        if self._policy.version != envelope.policy_version:
            return ActionState.FAILED, "policy_version_changed"
        decision = self._policy.evaluate(
            PolicyRequest(
                principal=ACTOR,
                action_type=envelope.action_type,
                environment=envelope.environment.value,
                target_id=envelope.target_id,
                arguments=dict(envelope.arguments),
                target_revision=envelope.target_revision,
            )
        )
        if decision.decision is not Decision.REQUIRES_APPROVAL:
            return ActionState.FAILED, f"policy_{decision.reason_code.value}"  # S-09
        if live_revision is None:
            return ActionState.FAILED, "target_unreachable"
        if live_revision != envelope.target_revision:
            return ActionState.FAILED, "stale_target_revision"  # S-04, R-08
        return None

    def _record(self, action_id: str, result: ApplyResult) -> Result:
        with self._factory.begin() as session:
            action = session.get_one(ActionProposalRow, action_id, with_for_update=True)
            operation = session.scalars(
                select(ExternalOperationRow)
                .where(ExternalOperationRow.action_id == action_id)
                .with_for_update()
            ).one()
            operation.status = result.status.value
            operation.receipt = {**result.receipt, "detail": result.detail}
            if result.status is ApplyStatus.COMPLETED:
                return self._succeed(session, action, "action_succeeded")
            if result.status is ApplyStatus.REJECTED:
                return self._fail_after_dispatch(session, action, f"rejected_{result.detail}")
            transition(
                session,
                ActionProposalRow,
                action.id,
                expected_state=ActionState.EXECUTING,
                expected_version=action.version,
                target_state=ActionState.RECONCILING,
                audit=self._audit("outcome_unknown", action, result.detail),
                outbox=OutboxSpec(
                    kind=RECONCILE_ACTION_JOB,
                    payload={"action_id": action.id},
                    dedup_key=f"{RECONCILE_ACTION_JOB}:{action.id}",
                ),
            )
            return Result(ExecutionOutcome.RECONCILING, result.detail)

    def _succeed(self, session: Session, action: ActionProposalRow, event: str) -> Result:
        transition(
            session,
            ActionProposalRow,
            action.id,
            expected_state=ActionState(action.state),
            expected_version=action.version,
            target_state=ActionState.SUCCEEDED,
            audit=self._audit(event, action),
        )
        incident = session.get_one(IncidentRow, action.incident_id, with_for_update=True)
        transition(
            session,
            IncidentRow,
            incident.id,
            expected_state=IncidentState.EXECUTING,
            expected_version=incident.version,
            target_state=IncidentState.VERIFYING,
            audit=self._audit("verification_requested", action),
            outbox=OutboxSpec(
                kind=VERIFY_ACTION_JOB,
                payload={"action_id": action.id},
                dedup_key=f"{VERIFY_ACTION_JOB}:{action.id}",
            ),
        )
        return Result(ExecutionOutcome.SUCCEEDED)

    def _fail_after_dispatch(
        self, session: Session, action: ActionProposalRow, reason: str
    ) -> Result:
        transition(
            session,
            ActionProposalRow,
            action.id,
            expected_state=ActionState(action.state),
            expected_version=action.version,
            target_state=ActionState.FAILED,
            audit=self._audit("action_failed", action, reason),
            extra_values={"reason_code": reason[:64]},
        )
        self._escalate(session, action, "action_failed", {IncidentState.EXECUTING})
        return Result(ExecutionOutcome.FAILED, reason)

    async def reconcile(self, action_id: str, give_up: bool = False) -> Result:
        """Resolve an in-flight or ambiguous action by ``action_id`` lookup only."""
        with self._factory() as session:
            action = session.get_one(ActionProposalRow, action_id)
            envelope = stored_envelope(action)
            adapter = self._adapters.get(action.action_type)
        if adapter is None:
            raise ReconcileRetryError("no adapter")
        lookup = await adapter.get_operation(envelope)
        with self._factory.begin() as session:
            action = session.get_one(ActionProposalRow, action_id, with_for_update=True)
            if ActionState(action.state) not in {ActionState.EXECUTING, ActionState.RECONCILING}:
                return Result(ExecutionOutcome.SKIPPED, f"state {action.state}")
            if action.state == ActionState.EXECUTING:
                # Crash between dispatch commit and receipt: make the gap explicit.
                transition(
                    session,
                    ActionProposalRow,
                    action.id,
                    expected_state=ActionState.EXECUTING,
                    expected_version=action.version,
                    target_state=ActionState.RECONCILING,
                    audit=self._audit("outcome_unknown", action, "executor_restarted"),
                )
                session.refresh(action)
            operation = session.scalars(
                select(ExternalOperationRow).where(ExternalOperationRow.action_id == action_id)
            ).one()
            if lookup.status is LookupStatus.FOUND and lookup.record.get("status") == "COMPLETED":
                operation.status = "COMPLETED"
                operation.receipt = {"operation": lookup.record, "reconciled": True}
                return self._succeed(session, action, "action_reconciled")
            if lookup.status is LookupStatus.NOT_FOUND:
                # Confirmed: the controller never applied this action ID.
                operation.status = "NOT_APPLIED"
                return self._fail_after_dispatch(session, action, "not_applied")
            if give_up:
                transition(
                    session,
                    ActionProposalRow,
                    action.id,
                    expected_state=ActionState.RECONCILING,
                    expected_version=action.version,
                    target_state=ActionState.ESCALATED,
                    audit=self._audit("outcome_unresolvable", action),
                    extra_values={"reason_code": "outcome_unknown"},
                )
                self._escalate(session, action, "outcome_unknown", {IncidentState.EXECUTING})
                return Result(ExecutionOutcome.ESCALATED, "outcome_unknown")
        raise ReconcileRetryError("operation status unknown")


def receipt_generation(receipt: Mapping[str, Any] | None) -> str | None:
    """Target revision reported by the controller after the action, if present.

    Consumer receipts nest it as ``operation.generation_after``; lab controller
    receipts report ``revision_after`` (directly or under ``operation`` once
    reconciled).
    """
    if not receipt:
        return None
    candidates: list[Any] = [receipt.get("revision_after")]
    operation = receipt.get("operation")
    if isinstance(operation, Mapping):
        candidates += [operation.get("generation_after"), operation.get("revision_after")]
    for value in candidates:
        if isinstance(value, str):
            return value
    return None
