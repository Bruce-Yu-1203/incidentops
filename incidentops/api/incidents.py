"""Read models for incident list and detail views."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from incidentops.api.investigations import RunSummary, latest_diagnosis, runs_for_incident
from incidentops.domain.state_machines import IncidentState
from incidentops.persistence.models import (
    AgentStepRow,
    AlertEventRow,
    AuditEventRow,
    IncidentRow,
    InvestigationRunRow,
)


class IncidentSummary(BaseModel):
    id: str
    dedup_key: str
    service_id: str
    environment: str
    severity: str
    state: IncidentState
    version: int
    created_at: datetime
    updated_at: datetime


class AlertEventView(BaseModel):
    id: str
    status: str
    alert_name: str
    fingerprint: str
    starts_at: str
    received_at: datetime
    labels: dict[str, str]
    annotations: dict[str, str]


class TimelineEntry(BaseModel):
    at: datetime
    kind: str
    summary: str
    reference_id: str


class IncidentDetail(IncidentSummary):
    escalation_reason: str | None
    alert_events: list[AlertEventView]
    timeline: list[TimelineEntry]
    runs: list[RunSummary]
    diagnosis: dict[str, Any] | None


def _summary(row: IncidentRow) -> IncidentSummary:
    return IncidentSummary(
        id=row.id,
        dedup_key=row.dedup_key,
        service_id=row.service_id,
        environment=row.environment,
        severity=row.severity,
        state=IncidentState(row.state),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def list_incidents(
    session: Session, state: IncidentState | None, service_id: str | None, limit: int
) -> list[IncidentSummary]:
    query = select(IncidentRow).order_by(IncidentRow.created_at.desc()).limit(limit)
    if state is not None:
        query = query.where(IncidentRow.state == state.value)
    if service_id is not None:
        query = query.where(IncidentRow.service_id == service_id)
    return [_summary(row) for row in session.scalars(query)]


def _alert_view(row: AlertEventRow) -> AlertEventView:
    payload: dict[str, Any] = row.payload
    return AlertEventView(
        id=row.id,
        status=row.status,
        alert_name=str(payload.get("alert_name", "")),
        fingerprint=row.fingerprint,
        starts_at=str(payload.get("starts_at", "")),
        received_at=row.received_at,
        labels=dict(payload.get("labels", {})),
        annotations=dict(payload.get("annotations", {})),
    )


def get_incident(session: Session, incident_id: str) -> IncidentDetail | None:
    row = session.get(IncidentRow, incident_id)
    if row is None:
        return None
    alerts = list(
        session.scalars(
            select(AlertEventRow)
            .where(AlertEventRow.incident_id == incident_id)
            .order_by(AlertEventRow.received_at)
        )
    )
    audits = list(
        session.scalars(
            select(AuditEventRow)
            .where(
                AuditEventRow.resource_type == "incidents",
                AuditEventRow.resource_id == incident_id,
            )
            .order_by(AuditEventRow.seq)
        )
    )
    timeline = [
        TimelineEntry(
            at=a.received_at,
            kind="alert",
            summary=f"{a.status} delivery ({a.fingerprint})",
            reference_id=a.id,
        )
        for a in alerts
    ] + [
        TimelineEntry(
            at=e.occurred_at,
            kind="audit",
            summary=f"{e.event_type}: {e.decision or ''}".strip(),
            reference_id=e.id,
        )
        for e in audits
    ]
    tool_steps = session.scalars(
        select(AgentStepRow)
        .join(InvestigationRunRow, InvestigationRunRow.id == AgentStepRow.run_id)
        .where(InvestigationRunRow.incident_id == incident_id, AgentStepRow.kind == "tool_call")
        .order_by(AgentStepRow.started_at)
    ).all()
    timeline += [
        TimelineEntry(
            at=step.started_at,
            kind="tool",
            summary=f"{step.metadata_.get('tool')} {step.status}",
            reference_id=str(step.metadata_.get("evidence_id") or step.id),
        )
        for step in tool_steps
    ]
    timeline.sort(key=lambda entry: entry.at)
    return IncidentDetail(
        **_summary(row).model_dump(),
        escalation_reason=row.escalation_reason,
        alert_events=[_alert_view(a) for a in alerts],
        timeline=timeline,
        runs=runs_for_incident(session, incident_id),
        diagnosis=latest_diagnosis(session, incident_id),
    )
