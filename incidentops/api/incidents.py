"""Read models for incident list and detail views."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from incidentops.api.actions import ActionSummary, actions_for_incident
from incidentops.api.investigations import RunSummary, latest_diagnosis, runs_for_incident
from incidentops.domain.disposition import Disposition, needs_human
from incidentops.domain.state_machines import IncidentState
from incidentops.persistence.models import (
    AgentStepRow,
    AlertEventRow,
    AuditEventRow,
    EvidenceRow,
    IncidentChatMessageRow,
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
    title: str | None = None
    owner_username: str | None = None
    assigned_at: datetime | None = None
    disposition: Disposition | None = None
    disposition_note: str | None = None
    disposed_by: str | None = None
    disposed_at: datetime | None = None
    needs_human: bool = False


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
    actor: str | None = None
    event_type: str | None = None
    decision: str | None = None
    resource_type: str | None = None
    target_state: str | None = None


class IncidentDetail(IncidentSummary):
    escalation_reason: str | None
    alert_events: list[AlertEventView]
    timeline: list[TimelineEntry]
    runs: list[RunSummary]
    diagnosis: dict[str, Any] | None
    actions: list[ActionSummary]


def first_alert_names(session: Session, incident_ids: list[str]) -> dict[str, str]:
    """The alert name of each incident's first delivery (its display title)."""
    names: dict[str, str] = {}
    if not incident_ids:
        return names
    rows = session.execute(
        select(AlertEventRow.incident_id, AlertEventRow.payload)
        .where(AlertEventRow.incident_id.in_(incident_ids))
        .order_by(AlertEventRow.received_at)
    )
    for incident_id, payload in rows:
        name = payload.get("alert_name") if isinstance(payload, dict) else None
        if incident_id not in names and name:
            names[incident_id] = str(name)
    return names


def _summary(row: IncidentRow, title: str | None = None) -> IncidentSummary:
    disposition = Disposition(row.disposition) if row.disposition else None
    state = IncidentState(row.state)
    return IncidentSummary(
        id=row.id,
        dedup_key=row.dedup_key,
        service_id=row.service_id,
        environment=row.environment,
        severity=row.severity,
        state=state,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        title=title,
        owner_username=row.owner_username,
        assigned_at=row.assigned_at,
        disposition=disposition,
        disposition_note=row.disposition_note,
        disposed_by=row.disposed_by,
        disposed_at=row.disposed_at,
        needs_human=needs_human(state, disposition),
    )


def list_incidents(
    session: Session, state: IncidentState | None, service_id: str | None, limit: int
) -> list[IncidentSummary]:
    query = select(IncidentRow).order_by(IncidentRow.created_at.desc()).limit(limit)
    if state is not None:
        query = query.where(IncidentRow.state == state.value)
    if service_id is not None:
        query = query.where(IncidentRow.service_id == service_id)
    rows = list(session.scalars(query))
    titles = first_alert_names(session, [row.id for row in rows])
    return [_summary(row, titles.get(row.id)) for row in rows]


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


def chat_timeline(session: Session, incident_id: str) -> list[TimelineEntry]:
    """One entry per chat question and per answer, with what the answer added."""
    messages = session.scalars(
        select(IncidentChatMessageRow)
        .where(IncidentChatMessageRow.incident_id == incident_id)
        .order_by(IncidentChatMessageRow.created_at)
    ).all()
    run_ids = [m.run_id for m in messages if m.run_id]
    calls: dict[str, int] = {}
    found: dict[str, int] = {}
    if run_ids:
        for run_id, count in session.execute(
            select(InvestigationRunRow.id, InvestigationRunRow.tool_call_count).where(
                InvestigationRunRow.id.in_(run_ids)
            )
        ):
            calls[run_id] = count
        for run_id, count in session.execute(
            select(EvidenceRow.run_id, func.count())
            .where(EvidenceRow.run_id.in_(run_ids))
            .group_by(EvidenceRow.run_id)
        ):
            found[run_id] = count
    entries: list[TimelineEntry] = []
    for m in messages:
        if m.role == "user":
            entries.append(
                TimelineEntry(
                    at=m.created_at,
                    kind="chat",
                    summary=m.content[:200],
                    reference_id=m.id,
                    actor=m.author,
                    event_type="chat_question",
                )
            )
            continue
        checks = calls.get(m.run_id or "", 0)
        added = found.get(m.run_id or "", 0)
        entries.append(
            TimelineEntry(
                at=m.created_at,
                kind="chat",
                summary=f"answered after {checks} checks, added {added} evidence",
                reference_id=m.id,
                actor="chat-agent",
                event_type="chat_answer",
                decision="SUGGEST_REINVESTIGATION" if m.suggest_reinvestigation else None,
            )
        )
        if added:
            entries.append(
                TimelineEntry(
                    at=m.created_at,
                    kind="chat",
                    summary=f"added {added} evidence",
                    reference_id=m.run_id or m.id,
                    actor="chat-agent",
                    event_type="chat_evidence_added",
                    decision=str(added),
                )
            )
        if m.suggest_reinvestigation:
            entries.append(
                TimelineEntry(
                    at=m.created_at,
                    kind="chat",
                    summary="suggested a re-investigation",
                    reference_id=m.id,
                    actor="chat-agent",
                    event_type="chat_suggested_reinvestigation",
                )
            )
    return entries


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
                or_(
                    and_(
                        AuditEventRow.resource_type == "incidents",
                        AuditEventRow.resource_id == incident_id,
                    ),
                    AuditEventRow.correlation_ids["incident_id"].astext == incident_id,
                )
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
            actor=e.actor,
            event_type=e.event_type,
            decision=e.decision,
            resource_type=e.resource_type,
            target_state=e.correlation_ids.get("to_state"),
        )
        for e in audits
        # Chat turns appear once each, as chat entries below, not as run audits.
        if not e.event_type.startswith("chat_turn_")
    ]
    timeline += chat_timeline(session, incident_id)
    tool_steps = session.scalars(
        select(AgentStepRow)
        .join(InvestigationRunRow, InvestigationRunRow.id == AgentStepRow.run_id)
        .where(
            InvestigationRunRow.incident_id == incident_id,
            InvestigationRunRow.kind != "chat",
            AgentStepRow.kind == "tool_call",
        )
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
        **_summary(row, first_alert_names(session, [row.id]).get(row.id)).model_dump(),
        escalation_reason=row.escalation_reason,
        alert_events=[_alert_view(a) for a in alerts],
        timeline=timeline,
        runs=runs_for_incident(session, incident_id),
        diagnosis=latest_diagnosis(session, incident_id),
        actions=actions_for_incident(session, incident_id),
    )
