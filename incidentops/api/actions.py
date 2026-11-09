"""Read model for an action: envelope, approval, decisions, execution and verification."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from incidentops.persistence.models import (
    ActionProposalRow,
    ApprovalDecisionRow,
    ApprovalRequestRow,
    ExternalOperationRow,
    VerificationRow,
)


class ApprovalView(BaseModel):
    id: str
    state: str
    version: int
    expires_at: datetime
    requested_by: str


class DecisionView(BaseModel):
    approver_id: str
    decision: str
    action_hash: str
    reason: str | None
    decided_at: datetime


class OperationView(BaseModel):
    provider: str
    provider_operation_id: str | None
    status: str
    receipt: dict[str, Any] | None
    updated_at: datetime


class VerificationView(BaseModel):
    id: str
    outcome: str
    checks: list[dict[str, Any]]
    window_start: datetime
    window_end: datetime


class ActionDetail(BaseModel):
    id: str
    incident_id: str
    state: str
    version: int
    reason_code: str | None
    action_hash: str
    policy_version: str
    expires_at: datetime
    envelope: dict[str, Any]
    approval: ApprovalView | None
    decisions: list[DecisionView]
    operations: list[OperationView]
    verifications: list[VerificationView]


class ActionSummary(BaseModel):
    id: str
    action_type: str
    target_id: str
    state: str
    version: int
    expires_at: datetime


def actions_for_incident(session: Session, incident_id: str) -> list[ActionSummary]:
    rows = session.scalars(
        select(ActionProposalRow)
        .where(ActionProposalRow.incident_id == incident_id)
        .order_by(ActionProposalRow.created_at)
    ).all()
    return [
        ActionSummary(
            id=r.id,
            action_type=r.action_type,
            target_id=r.target_id,
            state=r.state,
            version=r.version,
            expires_at=r.expires_at,
        )
        for r in rows
    ]


def get_action(session: Session, action_id: str) -> ActionDetail | None:
    action = session.get(ActionProposalRow, action_id)
    if action is None:
        return None
    approval = session.scalars(
        select(ApprovalRequestRow)
        .where(ApprovalRequestRow.action_id == action_id)
        .order_by(ApprovalRequestRow.created_at.desc())
    ).first()
    decisions = (
        session.scalars(
            select(ApprovalDecisionRow)
            .where(ApprovalDecisionRow.approval_id == approval.id)
            .order_by(ApprovalDecisionRow.decided_at)
        ).all()
        if approval
        else []
    )
    operations = session.scalars(
        select(ExternalOperationRow).where(ExternalOperationRow.action_id == action_id)
    ).all()
    verifications = session.scalars(
        select(VerificationRow)
        .where(VerificationRow.action_id == action_id)
        .order_by(VerificationRow.created_at)
    ).all()
    return ActionDetail(
        id=action.id,
        incident_id=action.incident_id,
        state=action.state,
        version=action.version,
        reason_code=action.reason_code,
        action_hash=action.action_hash,
        policy_version=action.policy_version,
        expires_at=action.expires_at,
        envelope=action.envelope,
        approval=ApprovalView(
            id=approval.id,
            state=approval.state,
            version=approval.version,
            expires_at=approval.expires_at,
            requested_by=approval.requested_by,
        )
        if approval
        else None,
        decisions=[
            DecisionView(
                approver_id=d.approver_id,
                decision=d.decision,
                action_hash=d.action_hash,
                reason=d.reason,
                decided_at=d.decided_at,
            )
            for d in decisions
        ],
        operations=[
            OperationView(
                provider=o.provider,
                provider_operation_id=o.provider_operation_id,
                status=o.status,
                receipt=o.receipt,
                updated_at=o.updated_at,
            )
            for o in operations
        ],
        verifications=[
            VerificationView(
                id=v.id,
                outcome=v.outcome,
                checks=v.checks,
                window_start=v.window_start,
                window_end=v.window_end,
            )
            for v in verifications
        ],
    )
