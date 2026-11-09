"""Tests for optional OpenTelemetry configuration."""

from __future__ import annotations

from fault_lab import telemetry


def test_no_endpoint_returns_noop_tracer_without_trace_ids() -> None:
    tracer = telemetry.configure("lab-api", environ={})
    with tracer.start_as_current_span("noop"):
        assert telemetry.current_trace_id() is None
