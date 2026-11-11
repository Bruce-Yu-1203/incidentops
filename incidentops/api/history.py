"""Searchable incident history with outcome, timing and summary statistics."""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from incidentops.api.incidents import first_alert_names
from incidentops.domain.disposition import HANDOVER_STATES
from incidentops.domain.state_machines import IncidentState
from incidentops.persistence.models import (
    ActionProposalRow,
    ApprovalDecisionRow,
    ApprovalRequestRow,
    DiagnosisRow,
    IncidentRow,
    InvestigationRunRow,
    VerificationRow,
)

MAX_SCAN = 5000


class Outcome(StrEnum):
    AUTO_RESOLVED = "auto_resolved"
    CLOSED_BY_PERSON = "closed_by_person"
    NEEDS_PERSON = "needs_person"
    CANCELLED = "cancelled"
    OPEN = "open"


class HistoryRow(BaseModel):
    id: str
    opened_at: datetime
    title: str | None
    service_id: str
    severity: str
    state: IncidentState
    outcome: Outcome
    disposition: str | None
    root_cause_id: str | None
    confidence: str | None
    time_to_diagnosis_s: float | None
    time_to_recovery_s: float | None
    approved_by: str | None


class HistoryStats(BaseModel):
    count: int
    auto_resolved: int
    auto_resolved_pct: float | None
    median_time_to_diagnosis_s: float | None
    median_time_to_recovery_s: float | None


class HistoryPage(BaseModel):
    rows: list[HistoryRow]
    total: int
    page: int
    page_size: int
    stats: HistoryStats
    services: list[str]
    root_causes: list[str]


class HistoryFilter(BaseModel):
    q: str | None = None
    outcome: Outcome | None = None
    service_id: str | None = None
    root_cause_id: str | None = None
    confidence: str | None = None
    since: datetime | None = None


def outcome_for(state: IncidentState, disposition: str | None) -> Outcome:
    if state is IncidentState.RESOLVED:
        return Outcome.AUTO_RESOLVED
    if state is IncidentState.CANCELLED:
        return Outcome.CANCELLED
    if state in HANDOVER_STATES:
        return Outcome.CLOSED_BY_PERSON if disposition else Outcome.NEEDS_PERSON
    return Outcome.OPEN


def _seconds(start: datetime, end: datetime | None) -> float | None:
    if end is None:
        return None
    return round(max((end - start).total_seconds(), 0.0), 1)


def _median(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(statistics.median(present), 1) if present else None


def _first_diagnoses(
    session: Session, incident_ids: list[str]
) -> dict[str, tuple[datetime, dict[str, Any]]]:
    found: dict[str, tuple[datetime, dict[str, Any]]] = {}
    rows = session.execute(
        select(InvestigationRunRow.incident_id, DiagnosisRow.created_at, DiagnosisRow.report)
        .join(InvestigationRunRow, InvestigationRunRow.id == DiagnosisRow.run_id)
        .where(InvestigationRunRow.incident_id.in_(incident_ids))
        .order_by(DiagnosisRow.created_at.desc())
    )
    # Latest diagnosis wins for content; earliest time counts for TTD.
    earliest: dict[str, datetime] = {}
    for incident_id, created_at, report in rows:
        found.setdefault(incident_id, (created_at, report))
        earliest[incident_id] = created_at
    return {k: (earliest[k], v[1]) for k, v in found.items()}


def _recoveries(session: Session, incident_ids: list[str]) -> dict[str, datetime]:
    rows = session.execute(
        select(ActionProposalRow.incident_id, VerificationRow.created_at)
        .join(ActionProposalRow, ActionProposalRow.id == VerificationRow.action_id)
        .where(
            ActionProposalRow.incident_id.in_(incident_ids),
            VerificationRow.outcome == "PASSED",
        )
        .order_by(VerificationRow.created_at)
    )
    found: dict[str, datetime] = {}
    for incident_id, created_at in rows:
        found.setdefault(incident_id, created_at)
    return found


def _approvers(session: Session, incident_ids: list[str]) -> dict[str, str]:
    rows = session.execute(
        select(ActionProposalRow.incident_id, ApprovalDecisionRow.approver_id)
        .join(ApprovalRequestRow, ApprovalRequestRow.id == ApprovalDecisionRow.approval_id)
        .join(ActionProposalRow, ActionProposalRow.id == ApprovalRequestRow.action_id)
        .where(
            ActionProposalRow.incident_id.in_(incident_ids),
            ApprovalDecisionRow.decision == "APPROVED",
        )
        .order_by(ApprovalDecisionRow.decided_at)
    )
    found: dict[str, str] = {}
    for incident_id, approver in rows:
        found.setdefault(incident_id, str(approver).removeprefix("user:"))
    return found


def build_rows(session: Session, incidents: list[IncidentRow]) -> list[HistoryRow]:
    ids = [row.id for row in incidents]
    titles = first_alert_names(session, ids)
    diagnoses = _first_diagnoses(session, ids) if ids else {}
    recoveries = _recoveries(session, ids) if ids else {}
    approvers = _approvers(session, ids) if ids else {}
    rows: list[HistoryRow] = []
    for incident in incidents:
        state = IncidentState(incident.state)
        diagnosis_at, report = diagnoses.get(incident.id, (None, None))
        diagnosis = (report or {}).get("diagnosis", {})
        recovered_at = recoveries.get(incident.id)
        if recovered_at is None and incident.disposed_at is not None:
            recovered_at = incident.disposed_at
        rows.append(
            HistoryRow(
                id=incident.id,
                opened_at=incident.created_at,
                title=titles.get(incident.id),
                service_id=incident.service_id,
                severity=incident.severity,
                state=state,
                outcome=outcome_for(state, incident.disposition),
                disposition=incident.disposition,
                root_cause_id=diagnosis.get("root_cause_id"),
                confidence=diagnosis.get("confidence_level"),
                time_to_diagnosis_s=_seconds(incident.created_at, diagnosis_at),
                time_to_recovery_s=_seconds(incident.created_at, recovered_at),
                approved_by=approvers.get(incident.id),
            )
        )
    return rows


def _matches(row: HistoryRow, flt: HistoryFilter) -> bool:
    if flt.outcome is not None and row.outcome is not flt.outcome:
        return False
    if flt.service_id and row.service_id != flt.service_id:
        return False
    if flt.root_cause_id and row.root_cause_id != flt.root_cause_id:
        return False
    if flt.confidence and row.confidence != flt.confidence:
        return False
    if flt.q:
        needle = flt.q.lower()
        haystack = " ".join(
            str(v) for v in (row.id, row.title, row.service_id, row.root_cause_id) if v
        ).lower()
        if needle not in haystack:
            return False
    return True


def stats_for(rows: list[HistoryRow]) -> HistoryStats:
    auto = sum(1 for r in rows if r.outcome is Outcome.AUTO_RESOLVED)
    finished = [r for r in rows if r.outcome is not Outcome.OPEN]
    return HistoryStats(
        count=len(rows),
        auto_resolved=auto,
        auto_resolved_pct=round(100 * auto / len(finished), 1) if finished else None,
        median_time_to_diagnosis_s=_median(r.time_to_diagnosis_s for r in rows),
        median_time_to_recovery_s=_median(r.time_to_recovery_s for r in rows),
    )


def search(
    session: Session,
    flt: HistoryFilter,
    page: int,
    page_size: int,
    now: datetime,
) -> HistoryPage:
    query = select(IncidentRow).order_by(IncidentRow.created_at.desc()).limit(MAX_SCAN)
    since = flt.since or (now - timedelta(days=30))
    query = query.where(IncidentRow.created_at >= since)
    incidents = list(session.scalars(query))
    every = build_rows(session, incidents)
    matched = [r for r in every if _matches(r, flt)]
    start = (page - 1) * page_size
    return HistoryPage(
        rows=matched[start : start + page_size],
        total=len(matched),
        page=page,
        page_size=page_size,
        stats=stats_for(matched),
        services=sorted({r.service_id for r in every}),
        root_causes=sorted({r.root_cause_id for r in every if r.root_cause_id}),
    )
