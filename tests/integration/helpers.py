"""Shared helpers to drive incidents through ingestion and investigation."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from incidentops.agent.model import ModelTurn, ScriptedModelAdapter
from incidentops.persistence.models import DiagnosisRow
from incidentops.workers.investigation import process_one
from tests.integration.test_investigation_worker import OWNER, _ingest, _runner
from tests.unit.test_investigation_graph import lag_and_status, submit


class FakeInspector:
    def __init__(self, revision: str | None = "gen-2", paused: bool = True) -> None:
        self.revision = revision
        self.paused = paused
        self.calls = 0

    async def inspect(self, target_id: str) -> dict[str, Any] | None:
        self.calls += 1
        if self.revision is None:
            return None
        return {"revision": self.revision, "paused": self.paused}


async def diagnosed_incident(
    factory: sessionmaker[Session], final: Any = submit
) -> tuple[str, str]:
    """Return ``(incident_id, diagnosis_id)`` for an incident in DIAGNOSED."""
    incident_id = _ingest(factory)
    steps: list[Any] = [lag_and_status(), final]
    await process_one(factory, _runner(factory, ScriptedModelAdapter(steps)), OWNER, 60)
    with factory() as session:
        diagnosis_id = session.scalars(select(DiagnosisRow.id)).one()
    return incident_id, diagnosis_id


def production_proposal(messages: list[dict[str, Any]]) -> ModelTurn:
    turn = submit(messages)
    turn.tool_calls[0].arguments["proposed_action"]["environment"] = "production"
    return turn
