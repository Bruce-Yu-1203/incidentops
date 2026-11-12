"""Tool executors: turn validated inputs into bounded, provenance-tagged readings."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any, Final

from incidentops.domain.catalog import ServiceCatalog, ServiceEntry
from incidentops.domain.schemas import ArgumentValue, EvidenceSource
from incidentops.tools.backends import Series, TelemetryBackend
from incidentops.tools.specs import (
    ConfigChangesInput,
    ConsumerLagInput,
    ConsumerStatusInput,
    DeploymentsInput,
    PostgresHealthInput,
    QueryMetricsInput,
    RedisHealthInput,
    SearchLogsInput,
    SearchTracesInput,
    _Input,
)
from incidentops.tools.templates import TEMPLATES, TemplateNotApplicableError


class ScopeError(ValueError):
    """The request targets something outside the tool's registered scope."""


@dataclass(frozen=True)
class ToolOutput:
    source: EvidenceSource
    template_id: str
    parameters: dict[str, ArgumentValue]
    window_start: datetime
    window_end: datetime
    summary: str
    data: Any = field(default=None)


Executor = Callable[[_Input, ServiceCatalog, TelemetryBackend, datetime], Awaitable[ToolOutput]]


def _expect[T: _Input](args: _Input, model: type[T]) -> T:
    if not isinstance(args, model):
        raise TypeError(f"expected {model.__name__}, got {type(args).__name__}")
    return args


def _step(minutes: int) -> int:
    # Roughly 60 points per series regardless of the window.
    return max(5, minutes)


def summarize_series(series: list[Series], unit: str) -> tuple[str, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for item in series:
        values = [v for _, v in item.points]
        if not values:
            continue
        rows.append(
            {
                "labels": item.labels,
                "first": round(values[0], 6),
                "last": round(values[-1], 6),
                "min": round(min(values), 6),
                "max": round(max(values), 6),
                "samples": len(values),
                # Downsample to at most 20 points to bound the snapshot.
                "points": [
                    [int(ts), round(v, 6)]
                    for ts, v in item.points[:: max(1, len(item.points) // 20)]
                ],
            }
        )
    if not rows:
        return "no data", rows
    parts = [
        f"{','.join(f'{k}={v}' for k, v in r['labels'].items()) or 'total'}: "
        f"first={r['first']} last={r['last']} max={r['max']} {unit}"
        for r in rows[:5]
    ]
    return "; ".join(parts), rows


def _service(catalog: ServiceCatalog, service_id: str) -> ServiceEntry:
    if not catalog.is_registered(service_id):
        raise ScopeError(f"service {service_id} is not registered")
    return catalog.get(service_id)


async def query_metrics(
    args: _Input, catalog: ServiceCatalog, backend: TelemetryBackend, now: datetime
) -> ToolOutput:
    args = _expect(args, QueryMetricsInput)
    template = TEMPLATES.get(args.template_id)
    if template is None:
        raise ScopeError(f"unknown template {args.template_id}")
    service = _service(catalog, args.service_id)
    try:
        promql = template.render(service)
    except TemplateNotApplicableError as exc:
        raise ScopeError(str(exc)) from exc
    start = now - timedelta(minutes=args.lookback_minutes)
    series = await backend.prometheus_range(promql, start, now, _step(args.lookback_minutes))
    summary, rows = summarize_series(series, template.unit)
    return ToolOutput(
        EvidenceSource.PROMETHEUS,
        template.template_id,
        {"service_id": service.service_id, "lookback_minutes": args.lookback_minutes},
        start,
        now,
        f"{template.description} for {service.service_id}: {summary}",
        rows,
    )


_SAFE_LOG_TEXT: Final = re.compile(r"^[A-Za-z0-9 _.:/=-]*$")


async def search_logs(
    args: _Input, catalog: ServiceCatalog, backend: TelemetryBackend, now: datetime
) -> ToolOutput:
    args = _expect(args, SearchLogsInput)
    service = _service(catalog, args.service_id)
    logql = f'{{service_name="{service.service_id}"}}'
    if args.contains:
        # Only a conservative character set is allowed in the line filter, so
        # the search text cannot alter the LogQL expression.
        if not _SAFE_LOG_TEXT.match(args.contains):
            raise ScopeError("contains may only use letters, digits and _ .:/=- characters")
        logql += f' |= "{args.contains}"'
    start = now - timedelta(minutes=args.lookback_minutes)
    lines = await backend.loki_query(logql, start, now, args.limit)
    entries = [{"timestamp_ns": line.timestamp_ns, "line": line.line} for line in lines]
    return ToolOutput(
        EvidenceSource.LOKI,
        "service_logs",
        {
            "service_id": service.service_id,
            "lookback_minutes": args.lookback_minutes,
            "contains": args.contains,
            "limit": args.limit,
        },
        start,
        now,
        f"{len(entries)} log lines for {service.service_id}",
        entries,
    )


async def search_traces(
    args: _Input, catalog: ServiceCatalog, backend: TelemetryBackend, now: datetime
) -> ToolOutput:
    args = _expect(args, SearchTracesInput)
    service = _service(catalog, args.service_id)
    conditions = [f'resource.service.name="{service.service_id}"']
    if args.min_duration_ms:
        conditions.append(f"duration > {args.min_duration_ms}ms")
    if args.errors_only:
        conditions.append("status = error")
    traceql = "{ " + " && ".join(conditions) + " }"
    start = now - timedelta(minutes=args.lookback_minutes)
    traces = await backend.tempo_search(traceql, start, now, args.limit)
    rows = [
        {"trace_id": t.trace_id, "name": t.root_name, "duration_ms": t.duration_ms} for t in traces
    ]
    slowest = max((t.duration_ms for t in traces), default=0)
    return ToolOutput(
        EvidenceSource.TEMPO,
        "service_traces",
        {
            "service_id": service.service_id,
            "lookback_minutes": args.lookback_minutes,
            "min_duration_ms": args.min_duration_ms,
            "errors_only": args.errors_only,
        },
        start,
        now,
        f"{len(rows)} traces for {service.service_id}; slowest {slowest} ms",
        rows,
    )


def _consumer_by_group(catalog: ServiceCatalog, group: str) -> ServiceEntry:
    for service in catalog.services:
        if service.kafka is not None and service.kafka.consumer_group == group:
            return service
    raise ScopeError(f"consumer group {group} is not registered")


async def get_kafka_consumer_lag(
    args: _Input, catalog: ServiceCatalog, backend: TelemetryBackend, now: datetime
) -> ToolOutput:
    args = _expect(args, ConsumerLagInput)
    service = _consumer_by_group(catalog, args.consumer_group_id)
    template = TEMPLATES["kafka_group_lag_by_partition"]
    start = now - timedelta(minutes=args.lookback_minutes)
    series = await backend.prometheus_range(
        template.render(service), start, now, _step(args.lookback_minutes)
    )
    summary, rows = summarize_series(series, template.unit)
    total_last = sum(row["last"] for row in rows)
    total_first = sum(row["first"] for row in rows)
    trend = "increasing" if total_last > total_first else "not increasing"
    return ToolOutput(
        EvidenceSource.PROMETHEUS,
        template.template_id,
        {"consumer_group_id": args.consumer_group_id, "lookback_minutes": args.lookback_minutes},
        start,
        now,
        f"group {args.consumer_group_id} lag {total_first:.0f} -> {total_last:.0f} ({trend}); "
        f"{summary}",
        rows,
    )


async def get_consumer_status(
    args: _Input, catalog: ServiceCatalog, backend: TelemetryBackend, now: datetime
) -> ToolOutput:
    args = _expect(args, ConsumerStatusInput)
    service = _service(catalog, args.worker_id)
    if service.management_url is None:
        raise ScopeError(f"{service.service_id} has no status endpoint")
    raw = await backend.consumer_status(service.service_id, str(service.management_url))
    # Keep only the documented status fields.
    status = {k: raw.get(k) for k in ("consumer_id", "paused", "generation")}
    return ToolOutput(
        EvidenceSource.WORKER_STATUS,
        "consumer_status",
        {"worker_id": service.service_id},
        now,
        now,
        f"worker {status['consumer_id']} paused={status['paused']} "
        f"generation={status['generation']}",
        status,
    )


async def get_postgres_health(
    args: _Input, catalog: ServiceCatalog, backend: TelemetryBackend, now: datetime
) -> ToolOutput:
    args = _expect(args, PostgresHealthInput)
    service = _service(catalog, args.service_id)
    readings: dict[str, Any] = {}
    for template_id in (
        "postgres_up",
        "postgres_connections_by_state",
        "postgres_locks_by_mode",
        "postgres_max_transaction_seconds",
        "postgres_max_connections",
    ):
        try:
            query = TEMPLATES[template_id].render(service)
        except TemplateNotApplicableError as exc:
            raise ScopeError(str(exc)) from exc
        series = await backend.prometheus_instant(query)
        readings[template_id] = [
            {"labels": s.labels, "value": s.points[-1][1] if s.points else None} for s in series
        ]
    up = readings["postgres_up"][0]["value"] if readings["postgres_up"] else None
    locks = {
        r["labels"].get("mode", "?"): r["value"]
        for r in readings["postgres_locks_by_mode"]
        if r["value"]
    }
    longest = {
        r["labels"].get("state", "?"): r["value"]
        for r in readings["postgres_max_transaction_seconds"]
        if r["value"]
    }
    by_state = {
        r["labels"].get("state", "?"): r["value"]
        for r in readings["postgres_connections_by_state"]
        if r["value"] is not None
    }
    used = sum(by_state.values()) if by_state else None
    limits = [r["value"] for r in readings["postgres_max_connections"] if r["value"] is not None]
    limit = limits[0] if limits else None

    def count(value: float | None) -> str:
        return "?" if value is None else f"{value:.0f}"

    # The share is part of the reading, so no threshold has to live in the prompt.
    share = f" ({used / limit:.0%} of max)" if used is not None and limit else ""
    readings["connection_share"] = round(used / limit, 3) if used is not None and limit else None

    return ToolOutput(
        EvidenceSource.PROMETHEUS,
        "postgres_health",
        {"service_id": service.service_id},
        now,
        now,
        f"{service.service_id} up={up} locks={json.dumps(locks, sort_keys=True)} "
        f"longest_tx_seconds={json.dumps(longest, sort_keys=True)} "
        f"connections={count(used)}/{count(limit)}{share} idle={count(by_state.get('idle'))}",
        readings,
    )


REDIS_HEALTH_TEMPLATES: Final = (
    "redis_up",
    "redis_memory_used_bytes",
    "redis_connected_clients",
    "redis_commands_per_second",
)


async def get_redis_health(
    args: _Input, catalog: ServiceCatalog, backend: TelemetryBackend, now: datetime
) -> ToolOutput:
    args = _expect(args, RedisHealthInput)
    service = _service(catalog, args.service_id)
    readings: dict[str, float | None] = {}
    for template_id in REDIS_HEALTH_TEMPLATES:
        try:
            query = TEMPLATES[template_id].render(service)
        except TemplateNotApplicableError as exc:
            raise ScopeError(str(exc)) from exc
        series = await backend.prometheus_instant(query)
        # The exporter reports one series per Redis; "no data" stays None.
        values = [s.points[-1][1] for s in series if s.points]
        readings[template_id] = round(values[0], 3) if values else None

    def show(value: float | None, digits: int = 0) -> str:
        return "no data" if value is None else f"{value:.{digits}f}"

    memory = readings["redis_memory_used_bytes"]
    return ToolOutput(
        EvidenceSource.PROMETHEUS,
        "redis_health",
        {"service_id": service.service_id},
        now,
        now,
        f"{service.service_id} up={show(readings['redis_up'])} "
        f"memory_mb={show(None if memory is None else memory / 1e6, 1)} "
        f"connected_clients={show(readings['redis_connected_clients'])} "
        f"commands_per_second={show(readings['redis_commands_per_second'], 1)}",
        readings,
    )


async def get_recent_deployments(
    args: _Input, catalog: ServiceCatalog, backend: TelemetryBackend, now: datetime
) -> ToolOutput:
    args = _expect(args, DeploymentsInput)
    service = _service(catalog, args.service_id)
    if service.management_url is None:
        raise ScopeError(f"{service.service_id} has no deployment controller")
    raw = await backend.controller_get(
        service.service_id, str(service.management_url), f"/deployments/{service.service_id}"
    )
    history = [
        {"release_id": d.get("release_id"), "deployed_at": d.get("deployed_at")}
        for d in raw.get("history", [])[-10:]
        if isinstance(d, dict)
    ]
    current = raw.get("current_release")
    last = history[-1] if history else None
    summary = f"{service.service_id} runs {current}; " + (
        f"last change to {last['release_id']} at {last['deployed_at']}"
        if last
        else "no recent changes"
    )
    return ToolOutput(
        EvidenceSource.DEPLOYMENT_CONTROLLER,
        "recent_deployments",
        {"service_id": service.service_id},
        now - timedelta(hours=1),
        now,
        summary,
        {
            "current_release": current,
            "pinned_healthy_release": raw.get("pinned_healthy_release"),
            "history": history,
        },
    )


_CONFIG_TEXT: Final = re.compile(r"^[A-Za-z0-9_.:-]{0,64}$")


def _config_value(value: Any) -> str:
    """Config values are shown only when they are short, plain identifiers."""
    text = str(value)
    return text if _CONFIG_TEXT.match(text) else "<redacted>"


async def get_recent_config_changes(
    args: _Input, catalog: ServiceCatalog, backend: TelemetryBackend, now: datetime
) -> ToolOutput:
    args = _expect(args, ConfigChangesInput)
    service = _service(catalog, args.service_id)
    if service.management_url is None:
        raise ScopeError(f"{service.service_id} has no configuration controller")
    raw = await backend.controller_get(
        service.service_id, str(service.management_url), f"/config-changes/{service.service_id}"
    )
    history = [
        {
            "revision": _config_value(c.get("revision")),
            "changed_at": _config_value(c.get("changed_at")),
            "actor": _config_value(c.get("actor")),
            "changes": {
                _config_value(k): _config_value(v) for k, v in (c.get("changes") or {}).items()
            },
        }
        for c in raw.get("history", [])[-10:]
        if isinstance(c, dict)
    ]
    current = _config_value(raw.get("current_revision"))
    pinned = _config_value(raw.get("pinned_good_revision"))
    last = history[-1] if history else None
    summary = f"{service.service_id} config {current} (last known good {pinned}); " + (
        f"last change to {last['revision']} at {last['changed_at']} by {last['actor']}: "
        f"{', '.join(sorted(last['changes'])) or 'no keys'}"
        if last
        else "no recent changes"
    )
    return ToolOutput(
        EvidenceSource.DEPLOYMENT_CONTROLLER,
        "recent_config_changes",
        {"service_id": service.service_id},
        now - timedelta(hours=1),
        now,
        summary,
        {"current_revision": current, "pinned_good_revision": pinned, "history": history},
    )


EXECUTORS: Final[Mapping[str, Executor]] = MappingProxyType(
    {
        "query_metrics": query_metrics,
        "search_logs": search_logs,
        "search_traces": search_traces,
        "get_kafka_consumer_lag": get_kafka_consumer_lag,
        "get_consumer_status": get_consumer_status,
        "get_postgres_health": get_postgres_health,
        "get_recent_deployments": get_recent_deployments,
        "get_redis_health": get_redis_health,
        "get_recent_config_changes": get_recent_config_changes,
    }
)
