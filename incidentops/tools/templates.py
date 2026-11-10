"""Predeclared PromQL query templates (tech-stack §5.1).

The model chooses a ``template_id`` and a registered ``service_id``; the PromQL
text is assembled server-side from catalog values only. No model-provided
string is ever interpolated into a query.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from incidentops.domain.catalog import ServiceEntry, ServiceKind


class TemplateNotApplicableError(ValueError):
    """The template does not apply to the requested service kind."""


@dataclass(frozen=True)
class MetricTemplate:
    template_id: str
    description: str
    service_kinds: frozenset[ServiceKind]
    build: Callable[[ServiceEntry], str]
    unit: str

    def render(self, service: ServiceEntry) -> str:
        if service.kind not in self.service_kinds:
            raise TemplateNotApplicableError(
                f"{self.template_id} does not apply to {service.kind.value} services"
            )
        return self.build(service)


def _sid(service: ServiceEntry) -> str:
    return f'service_id="{service.service_id}"'


def _group(service: ServiceEntry) -> str:
    if service.kafka is None or service.kafka.consumer_group is None:
        raise TemplateNotApplicableError(f"{service.service_id} has no consumer group")
    return f'consumergroup="{service.kafka.consumer_group}"'


_API = frozenset({ServiceKind.HTTP_API})
_CONSUMER = frozenset({ServiceKind.KAFKA_CONSUMER})

_ALL: Final = (
    MetricTemplate(
        "api_latency_p95",
        "P95 request latency over 1m windows",
        _API,
        lambda s: (
            "histogram_quantile(0.95, sum by (le) "
            f"(rate(lab_api_http_request_duration_seconds_bucket{{{_sid(s)}}}[1m])))"
        ),
        "seconds",
    ),
    MetricTemplate(
        "api_error_ratio",
        "Share of HTTP responses with 5xx status over 1m windows",
        _API,
        lambda s: (
            # "or vector(0)": with no 5xx series yet the ratio is 0, not "no data".
            f'(sum(rate(lab_api_http_requests_total{{{_sid(s)}, status=~"5.."}}[1m])) '
            f"or vector(0)) / "
            f"clamp_min(sum(rate(lab_api_http_requests_total{{{_sid(s)}}}[1m])), 1e-9)"
        ),
        "ratio",
    ),
    MetricTemplate(
        "api_request_rate",
        "HTTP requests per second by status",
        _API,
        lambda s: f"sum by (status) (rate(lab_api_http_requests_total{{{_sid(s)}}}[1m]))",
        "requests/s",
    ),
    MetricTemplate(
        "kafka_group_lag",
        "Total consumer-group lag in messages",
        _CONSUMER,
        lambda s: f"sum(kafka_consumergroup_lag{{{_group(s)}}})",
        "messages",
    ),
    MetricTemplate(
        "kafka_group_lag_by_partition",
        "Consumer-group lag per partition",
        _CONSUMER,
        lambda s: f"sum by (partition) (kafka_consumergroup_lag{{{_group(s)}}})",
        "messages",
    ),
    MetricTemplate(
        "consumer_processing_rate",
        "Messages processed per second by the consumer",
        _CONSUMER,
        lambda s: f"rate(lab_consumer_messages_processed_total{{{_sid(s)}}}[1m])",
        "messages/s",
    ),
    MetricTemplate(
        "producer_send_rate",
        "Messages produced per second",
        frozenset({ServiceKind.KAFKA_PRODUCER}),
        lambda s: f"rate(lab_producer_messages_sent_total{{{_sid(s)}}}[1m])",
        "messages/s",
    ),
    MetricTemplate(
        "kafka_broker_count",
        "Number of brokers visible to the lag exporter",
        frozenset({ServiceKind.KAFKA_BROKER}),
        lambda s: f"kafka_brokers{{{_sid(s)}}}",
        "brokers",
    ),
    MetricTemplate(
        "postgres_up",
        "1 when the exporter can reach PostgreSQL",
        frozenset({ServiceKind.POSTGRES}),
        lambda s: f"pg_up{{{_sid(s)}}}",
        "bool",
    ),
    MetricTemplate(
        "postgres_connections_by_state",
        "Server connections grouped by state",
        frozenset({ServiceKind.POSTGRES}),
        lambda s: f"sum by (state) (pg_stat_activity_count{{{_sid(s)}}})",
        "connections",
    ),
    MetricTemplate(
        "postgres_locks_by_mode",
        "Held and awaited locks grouped by mode",
        frozenset({ServiceKind.POSTGRES}),
        lambda s: f"sum by (mode) (pg_locks_count{{{_sid(s)}}})",
        "locks",
    ),
    MetricTemplate(
        "postgres_max_transaction_seconds",
        "Longest running transaction duration by state",
        frozenset({ServiceKind.POSTGRES}),
        lambda s: f"max by (state) (pg_stat_activity_max_tx_duration{{{_sid(s)}}})",
        "seconds",
    ),
    MetricTemplate(
        "api_memory_bytes",
        "Resident memory of the API process",
        _API,
        lambda s: f"process_resident_memory_bytes{{{_sid(s)}}}",
        "bytes",
    ),
    MetricTemplate(
        "api_cpu_cores",
        "CPU cores used by the API process (1m rate)",
        _API,
        lambda s: f"rate(process_cpu_seconds_total{{{_sid(s)}}}[1m])",
        "cores",
    ),
    MetricTemplate(
        "api_release",
        "Release currently running (1 per release label)",
        _API,
        lambda s: f"lab_api_release_info{{{_sid(s)}}}",
        "release",
    ),
    MetricTemplate(
        "postgres_exclusive_locks",
        "Held or awaited ACCESS EXCLUSIVE locks",
        frozenset({ServiceKind.POSTGRES}),
        lambda s: f'sum(pg_locks_count{{{_sid(s)}, mode="accessexclusivelock"}})',
        "locks",
    ),
    MetricTemplate(
        "redis_up",
        "1 when the exporter can reach Redis",
        frozenset({ServiceKind.REDIS}),
        lambda s: f"redis_up{{{_sid(s)}}}",
        "bool",
    ),
    MetricTemplate(
        "redis_memory_used_bytes",
        "Redis memory usage",
        frozenset({ServiceKind.REDIS}),
        lambda s: f"redis_memory_used_bytes{{{_sid(s)}}}",
        "bytes",
    ),
)

TEMPLATES: Final[Mapping[str, MetricTemplate]] = MappingProxyType({t.template_id: t for t in _ALL})
