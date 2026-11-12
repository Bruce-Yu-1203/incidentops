"""API side of the incident chat and of human-requested re-investigations.

The API holds no model credentials, so it only records the question and an
``answer_chat`` outbox job in one transaction; the chat worker writes the
answer. Reading the thread also returns, for each answer, the tool calls its
chat run made, and the steps of a turn that is still running.

A re-investigation (ADR-023) is requested here and run by the investigation
worker. It is allowed only on an incident that automation handed to a person
and that nobody has closed yet, and only one can be open at a time.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from incidentops.auth.service import AuthenticatedUser
from incidentops.domain.disposition import Disposition, needs_human
from incidentops.domain.ids import new_id
from incidentops.domain.state_machines import IncidentState, RunState
from incidentops.persistence.models import (
    AgentStepRow,
    AuditEventRow,
    DiagnosisRow,
    EvidenceRow,
    IncidentChatMessageRow,
    IncidentRow,
    InvestigationRunRow,
    OutboxJobRow,
)

ANSWER_CHAT_JOB: Final = "answer_chat"
REINVESTIGATE_JOB: Final = "reinvestigate_incident"
MAX_QUESTION_CHARS: Final = 2000
MAX_REASON_CHARS: Final = 400
HISTORY_LIMIT: Final = 200
OPEN_RUN_STATES: Final = (RunState.QUEUED.value, RunState.RUNNING.value)


class ChatStepView(BaseModel):
    tool: str
    status: str
    arguments: dict[str, Any] | str | None
    summary: str | None
    evidence_id: str | None
    duration_ms: int | None


class ChatMessageView(BaseModel):
    id: str
    role: str
    author: str
    content: str
    reply_to: str | None
    cited_evidence_ids: list[str]
    records_checked: int
    duration_ms: int | None
    suggest_reinvestigation: bool
    created_at: datetime
    run_id: str | None = None
    diagnosis_id: str | None = None
    steps: list[ChatStepView] = []
    new_evidence_ids: list[str] = []


class RunningTurnView(BaseModel):
    run_id: str
    asked_by: str | None
    started_at: datetime | None
    steps: list[ChatStepView]


class ReinvestigationView(BaseModel):
    run_id: str | None
    state: str  # QUEUED (job waiting) | RUNNING | COMPLETED | FAILED | BUDGET_EXCEEDED
    requested_by: str | None
    started_at: datetime | None
    finished_at: datetime | None


class ChatThreadView(BaseModel):
    incident_id: str
    messages: list[ChatMessageView]
    pending: bool
    running: RunningTurnView | None = None
    latest_diagnosis_id: str | None = None
    can_reinvestigate: bool = False
    reinvestigation: ReinvestigationView | None = None


def _step_view(step: AgentStepRow) -> ChatStepView:
    meta = step.metadata_
    duration = (
        int((step.finished_at - step.started_at).total_seconds() * 1000)
        if step.finished_at
        else None
    )
    arguments = meta.get("arguments")
    return ChatStepView(
        tool=str(meta.get("tool") or "unknown"),
        status=step.status,
        arguments=arguments if isinstance(arguments, (dict, str)) else None,
        summary=meta.get("summary"),
        evidence_id=meta.get("evidence_id"),
        duration_ms=duration,
    )


def _steps_by_run(session: Session, run_ids: list[str]) -> dict[str, list[ChatStepView]]:
    found: dict[str, list[ChatStepView]] = {}
    if not run_ids:
        return found
    for step in session.scalars(
        select(AgentStepRow)
        .where(AgentStepRow.run_id.in_(run_ids), AgentStepRow.kind == "tool_call")
        .order_by(AgentStepRow.started_at)
    ):
        found.setdefault(step.run_id, []).append(_step_view(step))
    return found


def _view(
    row: IncidentChatMessageRow,
    steps: dict[str, list[ChatStepView]],
    evidence: dict[str, list[str]],
) -> ChatMessageView:
    return ChatMessageView(
        id=row.id,
        role=row.role,
        author=row.author,
        content=row.content,
        reply_to=row.reply_to,
        cited_evidence_ids=list(row.cited_evidence_ids),
        records_checked=row.records_checked,
        duration_ms=row.duration_ms,
        suggest_reinvestigation=row.suggest_reinvestigation,
        created_at=row.created_at,
        run_id=row.run_id,
        diagnosis_id=row.diagnosis_id,
        steps=steps.get(row.run_id, []) if row.run_id else [],
        new_evidence_ids=evidence.get(row.run_id, []) if row.run_id else [],
    )


def latest_diagnosis_id(session: Session, incident_id: str) -> str | None:
    return session.scalars(
        select(DiagnosisRow.id)
        .join(InvestigationRunRow, InvestigationRunRow.id == DiagnosisRow.run_id)
        .where(InvestigationRunRow.incident_id == incident_id)
        .order_by(DiagnosisRow.created_at.desc())
    ).first()


def reinvestigation_status(session: Session, incident_id: str) -> ReinvestigationView | None:
    run = session.scalars(
        select(InvestigationRunRow)
        .where(
            InvestigationRunRow.incident_id == incident_id,
            InvestigationRunRow.kind == "reinvestigation",
        )
        .order_by(InvestigationRunRow.created_at.desc())
    ).first()
    waiting = session.scalars(
        select(OutboxJobRow).where(
            OutboxJobRow.kind == REINVESTIGATE_JOB,
            OutboxJobRow.aggregate_id == incident_id,
            OutboxJobRow.status.in_(["PENDING", "LEASED"]),
        )
    ).first()
    if waiting is not None and (run is None or run.state not in OPEN_RUN_STATES):
        return ReinvestigationView(
            run_id=None,
            state="QUEUED",
            requested_by=str(waiting.payload.get("requested_by") or "") or None,
            started_at=None,
            finished_at=None,
        )
    if run is None:
        return None
    return ReinvestigationView(
        run_id=run.id,
        state=run.state,
        requested_by=run.requested_by,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _reinvestigation_open(view: ReinvestigationView | None) -> bool:
    return view is not None and view.state in ("QUEUED", *OPEN_RUN_STATES)


def thread(session: Session, incident_id: str) -> ChatThreadView | None:
    incident = session.get(IncidentRow, incident_id)
    if incident is None:
        return None
    rows = list(
        session.scalars(
            select(IncidentChatMessageRow)
            .where(IncidentChatMessageRow.incident_id == incident_id)
            .order_by(IncidentChatMessageRow.created_at, IncidentChatMessageRow.role.desc())
            .limit(HISTORY_LIMIT)
        )
    )
    answered = {r.reply_to for r in rows if r.reply_to}
    pending = any(r.role == "user" and r.id not in answered for r in rows)
    open_run = session.scalars(
        select(InvestigationRunRow).where(
            InvestigationRunRow.incident_id == incident_id,
            InvestigationRunRow.kind == "chat",
            InvestigationRunRow.state.in_(OPEN_RUN_STATES),
        )
    ).first()
    run_ids = [r.run_id for r in rows if r.run_id] + ([open_run.id] if open_run else [])
    steps = _steps_by_run(session, run_ids)
    evidence: dict[str, list[str]] = {}
    if run_ids:
        for evidence_id, run_id in session.execute(
            select(EvidenceRow.id, EvidenceRow.run_id)
            .where(EvidenceRow.run_id.in_(run_ids))
            .order_by(EvidenceRow.observed_at)
        ):
            evidence.setdefault(run_id, []).append(evidence_id)
    reinvestigation = reinvestigation_status(session, incident_id)
    disposition = Disposition(incident.disposition) if incident.disposition else None
    return ChatThreadView(
        incident_id=incident_id,
        messages=[_view(r, steps, evidence) for r in rows],
        pending=pending,
        running=RunningTurnView(
            run_id=open_run.id,
            asked_by=open_run.requested_by,
            started_at=open_run.started_at,
            steps=steps.get(open_run.id, []),
        )
        if open_run
        else None,
        latest_diagnosis_id=latest_diagnosis_id(session, incident_id),
        can_reinvestigate=needs_human(IncidentState(incident.state), disposition)
        and not _reinvestigation_open(reinvestigation),
        reinvestigation=reinvestigation,
    )


def ask(
    session: Session, incident_id: str, user: AuthenticatedUser, question: str
) -> ChatMessageView | None:
    """Store a question and queue exactly one answer job for it."""
    if session.get(IncidentRow, incident_id) is None:
        return None
    message_id = new_id("msg")
    row = IncidentChatMessageRow(
        id=message_id,
        incident_id=incident_id,
        role="user",
        author=user.principal,
        content=question.strip()[:MAX_QUESTION_CHARS],
        cited_evidence_ids=[],
    )
    session.add(row)
    session.add(
        OutboxJobRow(
            id=new_id("job"),
            kind=ANSWER_CHAT_JOB,
            aggregate_id=message_id,
            dedup_key=f"{ANSWER_CHAT_JOB}:{message_id}",
            payload={"message_id": message_id, "incident_id": incident_id},
        )
    )
    session.flush()
    session.refresh(row)
    return _view(row, {}, {})


class ReinvestigationOutcome(StrEnum):
    QUEUED = "QUEUED"
    NOT_FOUND = "NOT_FOUND"
    NOT_HANDED_OVER = "NOT_HANDED_OVER"
    ALREADY_RUNNING = "ALREADY_RUNNING"


def request_reinvestigation(
    session: Session,
    incident_id: str,
    user: AuthenticatedUser,
    reason: str | None,
    from_message_id: str | None,
) -> ReinvestigationOutcome:
    """Queue a re-investigation of a handed-over incident (operator roles only)."""
    incident = session.get(IncidentRow, incident_id, with_for_update=True)
    if incident is None:
        return ReinvestigationOutcome.NOT_FOUND
    disposition = Disposition(incident.disposition) if incident.disposition else None
    if not needs_human(IncidentState(incident.state), disposition):
        return ReinvestigationOutcome.NOT_HANDED_OVER
    if _reinvestigation_open(reinvestigation_status(session, incident_id)):
        return ReinvestigationOutcome.ALREADY_RUNNING
    text = (reason or "").strip()
    if not text and from_message_id:
        source = session.get(IncidentChatMessageRow, from_message_id)
        if source is not None and source.incident_id == incident_id:
            text = source.content
    text = text[:MAX_REASON_CHARS]
    count = session.scalar(
        select(func.count())
        .select_from(OutboxJobRow)
        .where(OutboxJobRow.kind == REINVESTIGATE_JOB, OutboxJobRow.aggregate_id == incident_id)
    )
    session.add(
        OutboxJobRow(
            id=new_id("job"),
            kind=REINVESTIGATE_JOB,
            aggregate_id=incident_id,
            dedup_key=f"{REINVESTIGATE_JOB}:{incident_id}:{(count or 0) + 1}",
            payload={
                "incident_id": incident_id,
                "requested_by": user.principal,
                "reason": text or None,
                "from_message_id": from_message_id,
            },
        )
    )
    session.add(
        AuditEventRow(
            id=new_id("aud"),
            actor=user.principal,
            event_type="reinvestigation_requested",
            resource_type=IncidentRow.__tablename__,
            resource_id=incident_id,
            decision="QUEUED",
            reason=text or None,
            correlation_ids={
                "incident_id": incident_id,
                "state": incident.state,
                **({"message_id": from_message_id} if from_message_id else {}),
            },
        )
    )
    return ReinvestigationOutcome.QUEUED
