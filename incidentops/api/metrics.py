"""Read-only live metric panels for the console (server-side Prometheus proxy).

The browser never sends PromQL. It asks for a range, and the API runs a fixed
allowlist of queries: the lab recording rules plus predeclared templates
rendered from catalog values. A panel turns "over" only when its latest value
crosses the same threshold the matching alert rule uses.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from incidentops.api.incidents import IncidentDetail, first_alert_names
from incidentops.domain.catalog import ServiceCatalog
from incidentops.domain.state_machines import INCIDENT_MACHINE
from incidentops.persistence.models import AlertEventRow, AuditEventRow, IncidentRow
from incidentops.tools.backends import TelemetryBackend
from incidentops.tools.templates import TEMPLATES

RANGES: Final = (15, 30, 60, 360)
DASHBOARD_PATH: Final = "/d/incidentops-lab"


@dataclass(frozen=True)
class _Query:
    name: str
    build: Callable[[ServiceCatalog], str]


def _template(template_id: str, service_id: str) -> Callable[[ServiceCatalog], str]:
    return lambda catalog: TEMPLATES[template_id].render(catalog.get(service_id))


def _template_query(catalog: ServiceCatalog) -> str:
    return TEMPLATES["postgres_connections_by_state"].render(catalog.get("lab-postgres"))


def _fixed(promql: str) -> Callable[[ServiceCatalog], str]:
    return lambda _catalog: promql


@dataclass(frozen=True)
class PanelSpec:
    panel_id: str
    title: str
    unit: str
    queries: tuple[_Query, ...]
    # Matches the alert rule in infra/prometheus/rules/lab.rules.yml; None: no alert.
    threshold: float | None = None
    threshold_direction: Literal["above", "below"] = "above"


PANELS: Final[tuple[PanelSpec, ...]] = (
    PanelSpec(
        "api_p95",
        "API latency P95",
        "seconds",
        (_Query("p95", _fixed("lab_api:request_duration_seconds:p95_1m")),),
        threshold=0.5,
    ),
    PanelSpec(
        "api_5xx",
        "API 5xx ratio",
        "ratio",
        (_Query("5xx", _fixed("lab_api:error_ratio_1m")),),
        threshold=0.05,
    ),
    PanelSpec(
        "consumer_lag",
        "Consumer lag",
        "messages",
        (_Query("lag", _fixed("sum(lab_kafka:consumergroup_lag:sum)")),),
        threshold=200,
    ),
    PanelSpec(
        "produce_consume",
        "Produce vs consume",
        "messages/s",
        (
            _Query("produce", _template("producer_send_rate", "demo-producer")),
            _Query("consume", _template("consumer_processing_rate", "persist-demo-consumer")),
        ),
    ),
    PanelSpec(
        "pg_connections",
        "PostgreSQL connections",
        "connections",
        (_Query("connections", lambda c: f"sum({_template_query(c)})"),),
    ),
    PanelSpec(
        "redis_memory",
        "Redis memory",
        "bytes",
        (_Query("memory", _template("redis_memory_used_bytes", "lab-redis")),),
    ),
    PanelSpec(
        "api_cpu",
        "API CPU usage",
        "cores",
        (_Query("cpu", _template("api_cpu_cores", "lab-api")),),
        threshold=0.6,
    ),
    PanelSpec(
        "api_memory",
        "API resident memory",
        "bytes",
        (_Query("memory", _template("api_memory_bytes", "lab-api")),),
    ),
    PanelSpec(
        "api_memory_growth",
        "API memory growth",
        "bytes/s",
        (
            _Query(
                "growth", _fixed('deriv(process_resident_memory_bytes{service_id="lab-api"}[2m])')
            ),
        ),
        threshold=1000000,
    ),
    PanelSpec(
        "redis_up",
        "Redis availability",
        "bool",
        (_Query("available", _template("redis_up", "lab-redis")),),
        threshold=0.5,
        threshold_direction="below",
    ),
    PanelSpec(
        "pg_locks",
        "PostgreSQL exclusive locks",
        "locks",
        (_Query("locks", _template("postgres_exclusive_locks", "lab-postgres")),),
    ),
)


class SeriesView(BaseModel):
    name: str
    points: list[tuple[float, float]]


class PanelView(BaseModel):
    panel_id: str
    title: str
    unit: str
    threshold: float | None
    threshold_direction: Literal["above", "below"] = "above"
    status: Literal["ok", "over", "unavailable", "no_data", "partial"]
    latest: float | None
    series: list[SeriesView]


class IncidentWindow(BaseModel):
    incident_id: str
    title: str | None
    service_id: str
    start: datetime
    end: datetime | None


class MetricsView(BaseModel):
    start: datetime
    end: datetime
    step_seconds: int
    grafana_dashboard_url: str
    panels: list[PanelView]
    incidents: list[IncidentWindow]


def step_for(minutes: int) -> int:
    """About 180 points per chart, never finer than the 5 s scrape interval."""
    return max(5, (minutes * 60) // 180)


async def load_panels(
    backend: TelemetryBackend,
    catalog: ServiceCatalog,
    start: datetime,
    end: datetime,
    step: int,
    panel_ids: set[str] | None = None,
) -> list[PanelView]:
    panels: list[PanelView] = []
    for spec in PANELS:
        if panel_ids is not None and spec.panel_id not in panel_ids:
            continue
        series: list[SeriesView] = []
        failed = False
        for query in spec.queries:
            try:
                result = await backend.prometheus_range(query.build(catalog), start, end, step)
            except Exception:
                failed = True
                continue
            points = [(ts, value) for item in result[:1] for ts, value in item.points]
            series.append(SeriesView(name=query.name, points=points))
        latest = next((s.points[-1][1] for s in series if s.points), None)
        if failed and not any(s.points for s in series):
            status: Literal["ok", "over", "unavailable", "no_data", "partial"] = "unavailable"
        elif not any(s.points for s in series):
            status = "no_data"
        elif failed or any(not s.points for s in series):
            status = "partial"
        elif (
            spec.threshold is not None
            and latest is not None
            and (
                latest > spec.threshold
                if spec.threshold_direction == "above"
                else latest < spec.threshold
            )
        ):
            status = "over"
        else:
            status = "ok"
        panels.append(
            PanelView(
                panel_id=spec.panel_id,
                title=spec.title,
                unit=spec.unit,
                threshold=spec.threshold,
                threshold_direction=spec.threshold_direction,
                status=status,
                latest=latest,
                series=series,
            )
        )
    return panels


def incident_windows(session: Session, start: datetime, end: datetime) -> list[IncidentWindow]:
    terminal = [s.value for s in INCIDENT_MACHINE.terminal_states]
    rows = list(
        session.scalars(
            select(IncidentRow)
            .where(
                IncidentRow.created_at <= end,
                or_(IncidentRow.state.notin_(terminal), IncidentRow.updated_at >= start),
            )
            .order_by(IncidentRow.created_at)
            .limit(50)
        )
    )
    ids = [r.id for r in rows]
    titles = first_alert_names(session, ids)
    endings: dict[str, datetime] = {}
    for audit in session.scalars(
        select(AuditEventRow)
        .where(AuditEventRow.resource_type == "incidents", AuditEventRow.resource_id.in_(ids))
        .order_by(AuditEventRow.seq)
    ):
        target_state = audit.correlation_ids.get("to_state") or audit.decision
        if target_state in terminal:
            endings.setdefault(audit.resource_id, audit.occurred_at)
    onsets: dict[str, datetime] = {r.id: r.created_at for r in rows}
    for alert in session.scalars(select(AlertEventRow).where(AlertEventRow.incident_id.in_(ids))):
        try:
            onset = datetime.fromisoformat(
                str(alert.payload.get("starts_at", "")).replace("Z", "+00:00")
            )
            if onset.tzinfo is not None:
                onsets[alert.incident_id] = min(onsets[alert.incident_id], onset)
        except ValueError:
            continue
    windows: list[IncidentWindow] = []
    for row in rows:
        boundary = endings.get(row.id, row.updated_at) if row.state in terminal else None
        if boundary is not None and boundary < start:
            continue
        windows.append(
            IncidentWindow(
                incident_id=row.id,
                title=titles.get(row.id),
                service_id=row.service_id,
                start=onsets[row.id],
                end=boundary,
            )
        )
    return windows


def window(minutes: int, now: datetime) -> tuple[datetime, datetime]:
    return now - timedelta(minutes=minutes), now


class MetricMarker(BaseModel):
    at: datetime
    label: str


class IncidentMetricsView(MetricsView):
    markers: list[MetricMarker]
    mode: Literal["live", "historical"]


def incident_metric_window(
    incident: IncidentDetail, now: datetime
) -> tuple[datetime, datetime, list[MetricMarker]]:
    """Anchor replay to alert time and the immutable terminal transition, not later edits."""
    starts = [incident.created_at]
    for alert in incident.alert_events:
        try:
            parsed = datetime.fromisoformat(alert.starts_at.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                starts.append(parsed)
        except ValueError:
            continue
    onset = min(starts)
    markers = [MetricMarker(at=onset, label="Alert started")]
    terminal = {s.value for s in INCIDENT_MACHINE.terminal_states}
    ended = next(
        (
            e.at
            for e in incident.timeline
            if e.resource_type == "incidents" and (e.target_state or e.decision) in terminal
        ),
        None,
    )
    for event in incident.timeline:
        if event.event_type == "incident_executing":
            markers.append(MetricMarker(at=event.at, label="Action dispatched"))
    if ended is not None:
        markers.append(MetricMarker(at=ended, label="Automation ended"))
    # Old records lacking an audit boundary use their recorded update, never today's window.
    if ended is None and incident.state.value in terminal:
        ended = incident.updated_at
    end = min(now, ended + timedelta(minutes=5)) if ended else now
    return onset - timedelta(minutes=5), max(end, onset), markers


def incident_panel_id(service_id: str, title: str | None) -> str | None:
    by_alert = {
        "lab_api_latency_p95_high": "api_p95",
        "lab_api_error_ratio_high": "api_5xx",
        "lab_api_memory_growth": "api_memory_growth",
        "lab_api_cpu_high": "api_cpu",
        "lab_redis_down": "redis_up",
        "lab_kafka_consumer_lag_high": "consumer_lag",
    }
    return by_alert.get(title or "") or {
        "lab-api": "api_p95",
        "lab-postgres": "pg_locks",
        "lab-redis": "redis_up",
        "persist-demo-consumer": "consumer_lag",
    }.get(service_id)
