"""OpenTelemetry for IncidentOps itself (tech-stack §5.2).

Agent model calls and tool attempts become spans whose trace/span IDs are
stored on the matching ``agent_step`` rows, linking domain trajectory records
to infrastructure traces. Export is enabled only when
``OTEL_EXPORTER_OTLP_ENDPOINT`` is set; otherwise spans are no-ops.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

TRACER_NAME = "incidentops"


def configure(service_name: str, environ: Mapping[str, str] = os.environ) -> None:
    if not environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)


def tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME)


def span_ids(span: trace.Span) -> tuple[str | None, str | None]:
    """Hex trace and span IDs of a recording span, or ``(None, None)``."""
    context = span.get_span_context()
    if not context.is_valid:
        return None, None
    return format(context.trace_id, "032x"), format(context.span_id, "016x")
