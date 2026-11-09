"""Read models for investigation runs: steps, evidence and source links (FR-016).

Linkage shown to users: incident -> run -> tool step -> evidence -> source
(Grafana Explore for Prometheus/Loki/Tempo evidence).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from incidentops.domain.catalog import ServiceCatalog, UnknownServiceError
from incidentops.persistence.models import (
    AgentStepRow,
    DiagnosisRow,
    EvidenceRow,
    InvestigationRunRow,
)
from incidentops.tools.templates import TEMPLATES, TemplateNotApplicableError


class StepView(BaseModel):
    id: str
    kind: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    metadata: dict[str, Any]


class EvidenceView(BaseModel):
    id: str
    source: str
    template_id: str
    parameters: dict[str, Any]
    window_start: datetime
    window_end: datetime
    observed_at: datetime
    digest: str
    summary: str
    truncated: bool
    source_url: str | None


class RunSummary(BaseModel):
    id: str
    incident_id: str
    state: str
    model_id: str | None
    prompt_version: str | None
    error_code: str | None
    step_count: int
    tool_call_count: int
    input_tokens: int
    output_tokens: int
    started_at: datetime | None
    finished_at: datetime | None


class RunDetail(RunSummary):
    steps: list[StepView]
    evidence: list[EvidenceView]
    diagnosis: dict[str, Any] | None


def run_summary(row: InvestigationRunRow) -> RunSummary:
    return RunSummary(
        id=row.id,
        incident_id=row.incident_id,
        state=row.state,
        model_id=row.model_id,
        prompt_version=row.prompt_version,
        error_code=row.error_code,
        step_count=row.step_count,
        tool_call_count=row.tool_call_count,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _explore_url(
    grafana_url: str, datasource: str, expr: str, start: datetime, end: datetime
) -> str:
    panes = {
        "a": {
            "datasource": datasource,
            "queries": [{"refId": "A", "expr": expr, "query": expr}],
            "range": {
                "from": str(int(start.timestamp() * 1000)),
                "to": str(int(end.timestamp() * 1000)),
            },
        }
    }
    return f"{grafana_url.rstrip('/')}/explore?schemaVersion=1&panes={quote(json.dumps(panes))}"


def source_url(row: EvidenceRow, catalog: ServiceCatalog, grafana_url: str) -> str | None:
    """Rebuild the server-side query for a Grafana link; never from stored model text."""
    params = row.query_ref.get("parameters", {})
    template_id = row.query_ref.get("template_id", "")
    service_id = params.get("service_id") or params.get("consumer_group_id")
    try:
        if row.source == "prometheus" and template_id in TEMPLATES and service_id:
            service = catalog.get(str(service_id))
            expr = TEMPLATES[template_id].render(service)
            return _explore_url(grafana_url, "prometheus", expr, row.window_start, row.window_end)
        if row.source == "loki" and service_id:
            expr = f'{{service_name="{catalog.get(str(service_id)).service_id}"}}'
            return _explore_url(grafana_url, "loki", expr, row.window_start, row.window_end)
        if row.source == "tempo" and service_id:
            expr = f'{{ resource.service.name="{catalog.get(str(service_id)).service_id}" }}'
            return _explore_url(grafana_url, "tempo", expr, row.window_start, row.window_end)
    except (UnknownServiceError, TemplateNotApplicableError):
        return None
    return None


def get_run(
    session: Session, run_id: str, catalog: ServiceCatalog, grafana_url: str
) -> RunDetail | None:
    run = session.get(InvestigationRunRow, run_id)
    if run is None:
        return None
    steps = session.scalars(
        select(AgentStepRow).where(AgentStepRow.run_id == run_id).order_by(AgentStepRow.started_at)
    ).all()
    evidence = session.scalars(
        select(EvidenceRow).where(EvidenceRow.run_id == run_id).order_by(EvidenceRow.observed_at)
    ).all()
    diagnosis = session.scalars(
        select(DiagnosisRow).where(DiagnosisRow.run_id == run_id).order_by(DiagnosisRow.created_at)
    ).first()
    return RunDetail(
        **run_summary(run).model_dump(),
        steps=[
            StepView(
                id=s.id,
                kind=s.kind,
                status=s.status,
                started_at=s.started_at,
                finished_at=s.finished_at,
                metadata=s.metadata_,
            )
            for s in steps
        ],
        evidence=[
            EvidenceView(
                id=e.id,
                source=e.source,
                template_id=str(e.query_ref.get("template_id", "")),
                parameters=dict(e.query_ref.get("parameters", {})),
                window_start=e.window_start,
                window_end=e.window_end,
                observed_at=e.observed_at,
                digest=e.digest,
                summary=e.summary,
                truncated=e.truncated,
                source_url=source_url(e, catalog, grafana_url),
            )
            for e in evidence
        ],
        diagnosis=diagnosis.report if diagnosis else None,
    )


def runs_for_incident(session: Session, incident_id: str) -> list[RunSummary]:
    rows = session.scalars(
        select(InvestigationRunRow)
        .where(InvestigationRunRow.incident_id == incident_id)
        .order_by(InvestigationRunRow.created_at)
    ).all()
    return [run_summary(r) for r in rows]


def latest_diagnosis(session: Session, incident_id: str) -> dict[str, Any] | None:
    row = session.scalars(
        select(DiagnosisRow)
        .join(InvestigationRunRow, InvestigationRunRow.id == DiagnosisRow.run_id)
        .where(InvestigationRunRow.incident_id == incident_id)
        .order_by(DiagnosisRow.created_at.desc())
    ).first()
    return row.report if row else None
