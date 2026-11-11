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

from incidentops.api.incidents import first_alert_names
from incidentops.domain.catalog import ServiceCatalog
from incidentops.domain.state_machines import INCIDENT_MACHINE
from incidentops.persistence.models import IncidentRow
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
)


class SeriesView(BaseModel):
    name: str
    points: list[tuple[float, float]]


class PanelView(BaseModel):
    panel_id: str
    title: str
    unit: str
    threshold: float | None
    status: Literal["ok", "over", "unavailable"]
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
    backend: TelemetryBackend, catalog: ServiceCatalog, start: datetime, end: datetime, step: int
) -> list[PanelView]:
    panels: list[PanelView] = []
    for spec in PANELS:
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
            status: Literal["ok", "over", "unavailable"] = "unavailable"
        elif spec.threshold is not None and latest is not None and latest > spec.threshold:
            status = "over"
        else:
            status = "ok"
        panels.append(
            PanelView(
                panel_id=spec.panel_id,
                title=spec.title,
                unit=spec.unit,
                threshold=spec.threshold,
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
    titles = first_alert_names(session, [r.id for r in rows])
    return [
        IncidentWindow(
            incident_id=row.id,
            title=titles.get(row.id),
            service_id=row.service_id,
            start=row.created_at,
            end=row.updated_at if row.state in terminal else None,
        )
        for row in rows
    ]


def window(minutes: int, now: datetime) -> tuple[datetime, datetime]:
    return now - timedelta(minutes=minutes), now
