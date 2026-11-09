"""Records every observable model call and tool attempt as an ``agent_step``.

Only redacted, bounded summaries of inputs and outputs are kept; hidden model
reasoning is never captured (FR-015, architecture §8).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from incidentops.domain.ids import new_id
from incidentops.evidence.redaction import redact, truncate_utf8
from incidentops.persistence.models import AgentStepRow

MAX_METADATA_BYTES = 4096


class StepKind(StrEnum):
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    DIAGNOSIS = "diagnosis"
    RUN = "run"


@dataclass(frozen=True)
class StepRecord:
    run_id: str
    kind: StepKind
    status: str
    started_at: datetime
    finished_at: datetime | None
    metadata: dict[str, Any] = field(default_factory=dict)
    trace_id: str | None = None
    span_id: str | None = None
    id: str = field(default_factory=lambda: new_id("step"))
    parent_step_id: str | None = None


def sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Redact and bound step metadata; oversize payloads are replaced by a preview."""
    raw = json.dumps(metadata, sort_keys=True, default=str, ensure_ascii=False)
    cleaned = redact(raw)
    if len(cleaned.encode("utf-8")) <= MAX_METADATA_BYTES:
        result: dict[str, Any] = json.loads(cleaned)
        return result
    preview, _ = truncate_utf8(cleaned, MAX_METADATA_BYTES - 64)
    return {"truncated": True, "preview": preview}


class TrajectorySink(Protocol):
    def record(self, step: StepRecord) -> None: ...


class InMemoryTrajectorySink:
    def __init__(self) -> None:
        self.steps: list[StepRecord] = []

    def record(self, step: StepRecord) -> None:
        self.steps.append(
            StepRecord(
                run_id=step.run_id,
                kind=step.kind,
                status=step.status,
                started_at=step.started_at,
                finished_at=step.finished_at,
                metadata=sanitize_metadata(step.metadata),
                trace_id=step.trace_id,
                span_id=step.span_id,
                id=step.id,
                parent_step_id=step.parent_step_id,
            )
        )


class DatabaseTrajectorySink:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def record(self, step: StepRecord) -> None:
        with self._factory.begin() as session:
            session.add(
                AgentStepRow(
                    id=step.id,
                    run_id=step.run_id,
                    parent_step_id=step.parent_step_id,
                    kind=step.kind.value,
                    status=step.status,
                    started_at=step.started_at,
                    finished_at=step.finished_at,
                    trace_id=step.trace_id,
                    span_id=step.span_id,
                    metadata_=sanitize_metadata(step.metadata),
                )
            )
