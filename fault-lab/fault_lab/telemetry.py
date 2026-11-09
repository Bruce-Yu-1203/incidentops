"""OpenTelemetry setup for Fault Lab services.

Traces and logs are exported over OTLP/HTTP only when
``OTEL_EXPORTER_OTLP_ENDPOINT`` is set; otherwise a no-op tracer is used, so
unit tests and local runs need no collector.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def configure(service_name: str, environ: Mapping[str, str] = os.environ) -> trace.Tracer:
    """Configure tracing and log export for ``service_name`` and return a tracer."""
    endpoint = environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return trace.get_tracer(service_name)

    from opentelemetry._logs import set_logger_provider
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

    resource = Resource.create(
        {"service.name": service_name, "deployment.environment": "fault-lab"}
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    set_logger_provider(logger_provider)
    logging.getLogger().addHandler(
        LoggingHandler(level=logging.INFO, logger_provider=logger_provider)
    )
    return trace.get_tracer(service_name)


def current_trace_id() -> str | None:
    """Hex trace ID of the active span, used to correlate logs and responses."""
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None
