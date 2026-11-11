"""Assign and close handed-over incidents (ADR-021).

Neither operation changes the incident state or runs an action. Each one
locks the incident row, checks role, handover state and version, bumps the
version and appends an audit event in the caller's transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from incidentops.auth.service import OPERATE_ROLES, AuthenticatedUser
from incidentops.domain.disposition import HANDOVER_STATES, Disposition
from incidentops.domain.ids import new_id
from incidentops.domain.state_machines import IncidentState
from incidentops.persistence.models import AuditEventRow, IncidentRow

ASSIGNED_EVENT: Final = "incident_assigned"
DISPOSED_EVENT: Final = "incident_disposed"


class HandlingOutcome(StrEnum):
    ASSIGNED = "assigned"
    CLOSED = "closed"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    NOT_HANDED_OVER = "not_handed_over"
    ALREADY_CLOSED = "already_closed"
    VERSION_CONFLICT = "version_conflict"


@dataclass(frozen=True)
class HandlingResult:
    outcome: HandlingOutcome
    version: int | None = None

    @property
    def ok(self) -> bool:
        return self.outcome in {HandlingOutcome.ASSIGNED, HandlingOutcome.CLOSED}


def _guard(
    session: Session, incident_id: str, user: AuthenticatedUser, expected_version: int
) -> HandlingResult | IncidentRow:
    if user.role not in OPERATE_ROLES:
        return HandlingResult(HandlingOutcome.FORBIDDEN)
    row = session.get(IncidentRow, incident_id, with_for_update=True)
    if row is None:
        return HandlingResult(HandlingOutcome.NOT_FOUND)
    if IncidentState(row.state) not in HANDOVER_STATES:
        return HandlingResult(HandlingOutcome.NOT_HANDED_OVER, row.version)
    if row.disposition is not None:
        return HandlingResult(HandlingOutcome.ALREADY_CLOSED, row.version)
    if row.version != expected_version:
        return HandlingResult(HandlingOutcome.VERSION_CONFLICT, row.version)
    return row


def _audit(
    session: Session,
    row: IncidentRow,
    user: AuthenticatedUser,
    event: str,
    decision: str,
    reason: str | None,
) -> None:
    session.add(
        AuditEventRow(
            id=new_id("aud"),
            actor=user.principal,
            event_type=event,
            resource_type=IncidentRow.__tablename__,
            resource_id=row.id,
            decision=decision,
            reason=reason,
            correlation_ids={"incident_id": row.id, "state": row.state},
        )
    )


def _bump(session: Session, row: IncidentRow, values: dict[str, object]) -> int:
    new_version = session.execute(
        update(IncidentRow)
        .where(IncidentRow.id == row.id, IncidentRow.version == row.version)
        .values(version=IncidentRow.version + 1, updated_at=func.now(), **values)
        .returning(IncidentRow.version)
    ).scalar_one()
    return int(new_version)


def assign(
    session: Session, incident_id: str, user: AuthenticatedUser, expected_version: int
) -> HandlingResult:
    """Make ``user`` the owner of a handed-over incident."""
    guarded = _guard(session, incident_id, user, expected_version)
    if isinstance(guarded, HandlingResult):
        return guarded
    version = _bump(session, guarded, {"owner_username": user.username, "assigned_at": func.now()})
    _audit(session, guarded, user, ASSIGNED_EVENT, user.username, None)
    session.flush()
    return HandlingResult(HandlingOutcome.ASSIGNED, version)


def close(
    session: Session,
    incident_id: str,
    user: AuthenticatedUser,
    expected_version: int,
    disposition: Disposition,
    note: str | None,
) -> HandlingResult:
    """Record how a person dealt with a handed-over incident. Runs no action."""
    guarded = _guard(session, incident_id, user, expected_version)
    if isinstance(guarded, HandlingResult):
        return guarded
    values: dict[str, object] = {
        "disposition": disposition.value,
        "disposition_note": note,
        "disposed_by": user.username,
        "disposed_at": func.now(),
    }
    if guarded.owner_username is None:
        values |= {"owner_username": user.username, "assigned_at": func.now()}
    version = _bump(session, guarded, values)
    _audit(session, guarded, user, DISPOSED_EVENT, disposition.value, note)
    session.flush()
    return HandlingResult(HandlingOutcome.CLOSED, version)
