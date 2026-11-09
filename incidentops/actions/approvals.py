"""Human approval decisions bound to the exact action hash (architecture §9.3).

Each decision runs in one transaction: lock action and approval rows, verify
role, state, version, hash and TTL, then write the decision, audit events and
(for approvals) exactly one ``execute_action`` outbox job.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from incidentops.auth.service import APPROVE_ROLES, OPERATE_ROLES, AuthenticatedUser
from incidentops.domain.ids import new_id
from incidentops.domain.state_machines import ActionState, ApprovalState, IncidentState
from incidentops.persistence.models import (
    ActionProposalRow,
    ApprovalDecisionRow,
    ApprovalRequestRow,
    AuditEventRow,
    IncidentRow,
)
from incidentops.persistence.transitions import AuditSpec, OutboxSpec, transition

EXECUTE_ACTION_JOB: Final = "execute_action"


class Outcome(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    STATE_CONFLICT = "state_conflict"
    VERSION_CONFLICT = "version_conflict"
    HASH_MISMATCH = "hash_mismatch"
    SEPARATION_OF_DUTIES = "separation_of_duties"


@dataclass(frozen=True)
class DecisionResult:
    outcome: Outcome
    action_state: str | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome in {Outcome.APPROVED, Outcome.REJECTED, Outcome.CANCELLED}


def _audit(
    user: AuthenticatedUser | str, event: str, action: ActionProposalRow, **extra: str
) -> AuditSpec:
    actor = user if isinstance(user, str) else user.principal
    return AuditSpec(
        actor=actor,
        event_type=event,
        action_hash=action.action_hash,
        policy_version=action.policy_version,
        correlation_ids={"incident_id": action.incident_id, **extra},
    )


def _lock(session: Session, action_id: str) -> tuple[ActionProposalRow, ApprovalRequestRow] | None:
    action = session.get(ActionProposalRow, action_id, with_for_update=True)
    if action is None:
        return None
    approval = session.scalars(
        select(ApprovalRequestRow)
        .where(ApprovalRequestRow.action_id == action_id)
        .order_by(ApprovalRequestRow.created_at.desc())
        .with_for_update()
    ).first()
    if approval is None:
        return None
    return action, approval


def _escalate_incident(session: Session, incident_id: str, reason: str, audit: AuditSpec) -> None:
    incident = session.get_one(IncidentRow, incident_id, with_for_update=True)
    if incident.state == IncidentState.AWAITING_APPROVAL:
        transition(
            session,
            IncidentRow,
            incident_id,
            expected_state=IncidentState.AWAITING_APPROVAL,
            expected_version=incident.version,
            target_state=IncidentState.ESCALATED,
            audit=audit,
            extra_values={"escalation_reason": reason},
        )


def _expire(
    session: Session, action: ActionProposalRow, approval: ApprovalRequestRow, actor: str
) -> None:
    audit = _audit(actor, "approval_expired", action)
    if approval.state == ApprovalState.PENDING:
        transition(
            session,
            ApprovalRequestRow,
            approval.id,
            expected_state=ApprovalState.PENDING,
            expected_version=approval.version,
            target_state=ApprovalState.EXPIRED,
            audit=audit,
        )
    transition(
        session,
        ActionProposalRow,
        action.id,
        expected_state=ActionState(action.state),
        expected_version=action.version,
        target_state=ActionState.EXPIRED,
        audit=audit,
        extra_values={"reason_code": "approval_expired"},
    )
    _escalate_incident(session, action.incident_id, "approval_expired", audit)


def approve(
    session: Session,
    action_id: str,
    user: AuthenticatedUser,
    action_hash: str,
    expected_version: int,
    now: datetime,
    reason: str | None = None,
    separation_of_duties: bool = True,
) -> DecisionResult:
    if user.role not in APPROVE_ROLES:
        return DecisionResult(Outcome.FORBIDDEN)
    locked = _lock(session, action_id)
    if locked is None:
        return DecisionResult(Outcome.NOT_FOUND)
    action, approval = locked
    if action.state != ActionState.PENDING_APPROVAL or approval.state != ApprovalState.PENDING:
        return DecisionResult(Outcome.STATE_CONFLICT, action.state)
    if action.version != expected_version:
        return DecisionResult(Outcome.VERSION_CONFLICT, action.state, f"current {action.version}")
    if not (
        hmac.compare_digest(action_hash, action.action_hash)
        and hmac.compare_digest(action.action_hash, approval.action_hash)
    ):
        return DecisionResult(Outcome.HASH_MISMATCH, action.state)
    if separation_of_duties and approval.requested_by == user.principal:
        return DecisionResult(Outcome.SEPARATION_OF_DUTIES, action.state)
    if now >= action.expires_at:
        _expire(session, action, approval, user.principal)
        return DecisionResult(Outcome.EXPIRED, ActionState.EXPIRED.value)

    audit = _audit(user, "approval_decided", action, approval_id=approval.id)
    transition(
        session,
        ApprovalRequestRow,
        approval.id,
        expected_state=ApprovalState.PENDING,
        expected_version=approval.version,
        target_state=ApprovalState.APPROVED,
        audit=audit,
    )
    session.add(
        ApprovalDecisionRow(
            id=new_id("dec"),
            approval_id=approval.id,
            approver_id=user.principal,
            decision="APPROVED",
            action_hash=action.action_hash,
            reason=reason,
        )
    )
    version = transition(
        session,
        ActionProposalRow,
        action.id,
        expected_state=ActionState.PENDING_APPROVAL,
        expected_version=action.version,
        target_state=ActionState.APPROVED,
        audit=audit,
    )
    transition(
        session,
        ActionProposalRow,
        action.id,
        expected_state=ActionState.APPROVED,
        expected_version=version,
        target_state=ActionState.QUEUED,
        audit=_audit(user, "action_queued", action),
        outbox=OutboxSpec(
            kind=EXECUTE_ACTION_JOB,
            payload={"action_id": action.id},
            dedup_key=f"{EXECUTE_ACTION_JOB}:{action.id}",
        ),
    )
    return DecisionResult(Outcome.APPROVED, ActionState.QUEUED.value)


def reject(
    session: Session,
    action_id: str,
    user: AuthenticatedUser,
    expected_version: int,
    reason: str | None = None,
) -> DecisionResult:
    if user.role not in APPROVE_ROLES:
        return DecisionResult(Outcome.FORBIDDEN)
    locked = _lock(session, action_id)
    if locked is None:
        return DecisionResult(Outcome.NOT_FOUND)
    action, approval = locked
    if action.state != ActionState.PENDING_APPROVAL or approval.state != ApprovalState.PENDING:
        return DecisionResult(Outcome.STATE_CONFLICT, action.state)
    if action.version != expected_version:
        return DecisionResult(Outcome.VERSION_CONFLICT, action.state)
    audit = _audit(user, "approval_decided", action, approval_id=approval.id)
    transition(
        session,
        ApprovalRequestRow,
        approval.id,
        expected_state=ApprovalState.PENDING,
        expected_version=approval.version,
        target_state=ApprovalState.REJECTED,
        audit=audit,
    )
    session.add(
        ApprovalDecisionRow(
            id=new_id("dec"),
            approval_id=approval.id,
            approver_id=user.principal,
            decision="REJECTED",
            action_hash=action.action_hash,
            reason=reason,
        )
    )
    transition(
        session,
        ActionProposalRow,
        action.id,
        expected_state=ActionState.PENDING_APPROVAL,
        expected_version=action.version,
        target_state=ActionState.REJECTED,
        audit=audit,
        extra_values={"reason_code": "approval_rejected"},
    )
    _escalate_incident(session, action.incident_id, "approval_rejected", audit)
    return DecisionResult(Outcome.REJECTED, ActionState.REJECTED.value)


CANCELLABLE: Final = {ActionState.PENDING_APPROVAL, ActionState.APPROVED, ActionState.QUEUED}


def cancel(
    session: Session,
    action_id: str,
    user: AuthenticatedUser,
    expected_version: int,
    reason: str | None = None,
) -> DecisionResult:
    if user.role not in OPERATE_ROLES:
        return DecisionResult(Outcome.FORBIDDEN)
    locked = _lock(session, action_id)
    if locked is None:
        return DecisionResult(Outcome.NOT_FOUND)
    action, approval = locked
    if ActionState(action.state) not in CANCELLABLE:
        return DecisionResult(Outcome.STATE_CONFLICT, action.state)
    if action.version != expected_version:
        return DecisionResult(Outcome.VERSION_CONFLICT, action.state)
    audit = _audit(user, "action_cancelled", action)
    if approval.state in {ApprovalState.PENDING, ApprovalState.APPROVED}:
        transition(
            session,
            ApprovalRequestRow,
            approval.id,
            expected_state=ApprovalState(approval.state),
            expected_version=approval.version,
            target_state=ApprovalState.REVOKED,
            audit=audit,
        )
    transition(
        session,
        ActionProposalRow,
        action.id,
        expected_state=ActionState(action.state),
        expected_version=action.version,
        target_state=ActionState.CANCELLED,
        audit=audit,
        extra_values={"reason_code": (reason or "cancelled_by_operator")[:64]},
    )
    _escalate_incident(session, action.incident_id, "action_cancelled", audit)
    return DecisionResult(Outcome.CANCELLED, ActionState.CANCELLED.value)


def expire_due(session: Session, now: datetime, actor: str, limit: int = 50) -> int:
    """Expire pending approvals past their TTL; returns how many were expired."""
    due = session.scalars(
        select(ActionProposalRow.id)
        .where(
            ActionProposalRow.state == ActionState.PENDING_APPROVAL.value,
            ActionProposalRow.expires_at <= now,
        )
        .limit(limit)
    ).all()
    expired = 0
    for action_id in due:
        locked = _lock(session, action_id)
        if locked is None:
            continue
        action, approval = locked
        if action.state == ActionState.PENDING_APPROVAL:
            _expire(session, action, approval, actor)
            expired += 1
    return expired


def record_refusal(
    session: Session,
    action_id: str,
    user: AuthenticatedUser,
    operation: str,
    result: DecisionResult,
) -> None:
    """Refused decision attempts are security events too (S-04, S-06)."""
    session.add(
        AuditEventRow(
            id=new_id("aud"),
            actor=user.principal,
            event_type=f"{operation}_refused",
            resource_type="action_proposals",
            resource_id=action_id[:128],
            decision=result.outcome.value,
            reason=result.detail,
            correlation_ids={"role": user.role.value},
        )
    )
