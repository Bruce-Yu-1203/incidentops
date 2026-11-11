"""API side of the incident chat: store a question and queue its answer.

The API holds no model credentials, so it only records the question and an
``answer_chat`` outbox job in one transaction. The investigation worker, which
already has the read-only model identity, writes the answer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from incidentops.auth.service import AuthenticatedUser
from incidentops.domain.ids import new_id
from incidentops.persistence.models import IncidentChatMessageRow, IncidentRow, OutboxJobRow

ANSWER_CHAT_JOB: Final = "answer_chat"
MAX_QUESTION_CHARS: Final = 2000
HISTORY_LIMIT: Final = 200


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


class ChatThreadView(BaseModel):
    incident_id: str
    messages: list[ChatMessageView]
    pending: bool


def _view(row: IncidentChatMessageRow) -> ChatMessageView:
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
    )


def thread(session: Session, incident_id: str) -> ChatThreadView | None:
    if session.get(IncidentRow, incident_id) is None:
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
    return ChatThreadView(
        incident_id=incident_id, messages=[_view(r) for r in rows], pending=pending
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
    return _view(row)
