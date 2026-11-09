"""Compare-and-swap lifecycle transitions with audit and outbox writes.

Contract (architecture §5): update ``WHERE id = :id AND state = :expected AND
version = :v``, increment ``version`` and, in the *same transaction*, append the
audit event and any outbox job. A zero-row update is a conflict that callers
surface as HTTP 409; it is never retried silently.

These helpers only add statements to the caller's session. The caller owns the
transaction boundary and commits once, so partial writes cannot be observed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from incidentops.domain.ids import new_id
from incidentops.domain.state_machines import (
    ACTION_MACHINE,
    APPROVAL_MACHINE,
    INCIDENT_MACHINE,
    RUN_MACHINE,
    StateMachine,
)
from incidentops.persistence.models import (
    ActionProposalRow,
    ApprovalRequestRow,
    AuditEventRow,
    Base,
    IncidentRow,
    InvestigationRunRow,
    OutboxJobRow,
)

_MACHINES: dict[type[Base], StateMachine[Any]] = {
    IncidentRow: INCIDENT_MACHINE,
    InvestigationRunRow: RUN_MACHINE,
    ActionProposalRow: ACTION_MACHINE,
    ApprovalRequestRow: APPROVAL_MACHINE,
}


class TransitionConflictError(RuntimeError):
    """The row was not in the expected state/version (maps to HTTP 409)."""

    def __init__(self, table: str, row_id: str, expected_state: str, expected_version: int):
        super().__init__(
            f"{table}/{row_id}: expected state {expected_state} at version {expected_version}"
        )
        self.table = table
        self.row_id = row_id


@dataclass(frozen=True)
class AuditSpec:
    actor: str
    event_type: str
    reason: str | None = None
    decision: str | None = None
    action_hash: str | None = None
    policy_version: str | None = None
    correlation_ids: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class OutboxSpec:
    kind: str
    payload: dict[str, Any]
    # Defaults to "<kind>:<row id>:<new version>" so a duplicated transition
    # attempt can never enqueue a second job for the same state change.
    dedup_key: str | None = None


def transition[S: StrEnum](
    session: Session,
    model: type[IncidentRow | InvestigationRunRow | ActionProposalRow | ApprovalRequestRow],
    row_id: str,
    *,
    expected_state: S,
    expected_version: int,
    target_state: S,
    audit: AuditSpec,
    outbox: OutboxSpec | None = None,
    extra_values: dict[str, Any] | None = None,
) -> int:
    """Apply one guarded transition and return the new version."""
    machine = _MACHINES[model]
    machine.ensure_transition(expected_state, target_state)

    values: dict[str, Any] = {
        "state": target_state.value,
        "version": model.version + 1,
        "updated_at": func.now(),
        **(extra_values or {}),
    }
    statement = (
        update(model)
        .where(
            model.id == row_id,
            model.state == expected_state.value,
            model.version == expected_version,
        )
        .values(**values)
        .returning(model.version)
    )
    new_version = session.execute(statement).scalar_one_or_none()
    if new_version is None:
        raise TransitionConflictError(
            model.__tablename__, row_id, expected_state.value, expected_version
        )

    session.add(
        AuditEventRow(
            id=new_id("aud"),
            actor=audit.actor,
            event_type=audit.event_type,
            resource_type=model.__tablename__,
            resource_id=row_id,
            action_hash=audit.action_hash,
            decision=audit.decision or target_state.value,
            reason=audit.reason,
            policy_version=audit.policy_version,
            correlation_ids={
                **audit.correlation_ids,
                "from_state": expected_state.value,
                "to_state": target_state.value,
            },
        )
    )
    if outbox is not None:
        session.add(
            OutboxJobRow(
                id=new_id("job"),
                kind=outbox.kind,
                aggregate_id=row_id,
                dedup_key=outbox.dedup_key or f"{outbox.kind}:{row_id}:{new_version}",
                payload=outbox.payload,
            )
        )
    session.flush()
    return int(new_version)
