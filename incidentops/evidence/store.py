"""Evidence persistence with provenance, digests and bounded redacted snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from incidentops.domain.ids import new_id
from incidentops.domain.schemas import (
    MAX_SNAPSHOT_BYTES,
    ArgumentValue,
    Evidence,
    EvidenceSource,
    QueryReference,
)
from incidentops.evidence.redaction import redact, truncate_utf8
from incidentops.persistence.models import EvidenceRow


class UnresolvedEvidenceError(ValueError):
    """A diagnosis cites evidence IDs that do not exist for its run."""

    def __init__(self, missing: set[str]) -> None:
        super().__init__(f"unresolved evidence ids: {sorted(missing)}")
        self.missing = missing


@dataclass(frozen=True)
class EvidenceDraft:
    incident_id: str
    run_id: str
    source: EvidenceSource
    template_id: str
    parameters: dict[str, ArgumentValue]
    window_start: datetime
    window_end: datetime
    observed_at: datetime
    summary: str
    data: Any


def build_evidence(draft: EvidenceDraft) -> Evidence:
    """Redact, bound and digest a reading. The digest covers the stored snapshot."""
    raw = json.dumps(draft.data, sort_keys=True, default=str, ensure_ascii=False)
    snapshot, truncated = truncate_utf8(redact(raw), MAX_SNAPSHOT_BYTES)
    summary, _ = truncate_utf8(redact(draft.summary), 1900)
    digest = "sha256:" + hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
    return Evidence(
        id=new_id("ev"),
        incident_id=draft.incident_id,
        run_id=draft.run_id,
        source=draft.source,
        query=QueryReference(template_id=draft.template_id, parameters=draft.parameters),
        window_start=draft.window_start,
        window_end=draft.window_end,
        observed_at=draft.observed_at,
        digest=digest,
        summary=summary,
        snapshot=snapshot,
        truncated=truncated,
    )


class EvidenceSink(Protocol):
    def save(self, evidence: Evidence) -> None: ...

    def resolve(self, run_id: str, evidence_ids: Iterable[str]) -> list[Evidence]: ...


class InMemoryEvidenceSink:
    """Test double with the same resolution semantics as the database store."""

    def __init__(self) -> None:
        self.items: dict[str, Evidence] = {}

    def save(self, evidence: Evidence) -> None:
        self.items[evidence.id] = evidence

    def resolve(self, run_id: str, evidence_ids: Iterable[str]) -> list[Evidence]:
        wanted = set(evidence_ids)
        found = {i: e for i, e in self.items.items() if i in wanted and e.run_id == run_id}
        missing = wanted - set(found)
        if missing:
            raise UnresolvedEvidenceError(missing)
        return list(found.values())


class DatabaseEvidenceSink:
    """Evidence rows in the Control PostgreSQL."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def save(self, evidence: Evidence) -> None:
        with self._factory.begin() as session:
            session.add(
                EvidenceRow(
                    id=evidence.id,
                    incident_id=evidence.incident_id,
                    run_id=evidence.run_id,
                    source=evidence.source.value,
                    query_ref=evidence.query.model_dump(mode="json"),
                    window_start=evidence.window_start,
                    window_end=evidence.window_end,
                    observed_at=evidence.observed_at,
                    digest=evidence.digest,
                    summary=evidence.summary,
                    snapshot=evidence.snapshot,
                    truncated=evidence.truncated,
                )
            )

    def resolve(self, run_id: str, evidence_ids: Iterable[str]) -> list[Evidence]:
        wanted = set(evidence_ids)
        with self._factory() as session:
            rows = session.scalars(
                select(EvidenceRow).where(EvidenceRow.id.in_(wanted), EvidenceRow.run_id == run_id)
            ).all()
        missing = wanted - {row.id for row in rows}
        if missing:
            raise UnresolvedEvidenceError(missing)
        return [
            Evidence(
                id=row.id,
                incident_id=row.incident_id,
                run_id=row.run_id,
                source=EvidenceSource(row.source),
                query=QueryReference.model_validate(row.query_ref),
                window_start=row.window_start,
                window_end=row.window_end,
                observed_at=row.observed_at,
                digest=row.digest,
                summary=row.summary,
                snapshot=row.snapshot,
                truncated=row.truncated,
            )
            for row in rows
        ]
