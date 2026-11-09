"""Trajectory steps carry the OTel trace/span IDs of their spans (FR-015/016)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from incidentops import telemetry
from incidentops.agent.model import ScriptedModelAdapter
from tests.unit.test_investigation_graph import lag_and_status, run, submit


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    monkeypatch.setattr(telemetry, "tracer", lambda: provider.get_tracer("test"))
    yield memory
    provider.shutdown()


async def test_steps_link_to_spans(exporter: InMemorySpanExporter) -> None:
    final, trajectory = await run(ScriptedModelAdapter([lag_and_status(), submit]))
    assert final["status"] == "COMPLETED"
    spans = {format(s.context.span_id, "016x"): s for s in exporter.get_finished_spans()}
    linked = [s for s in trajectory.steps if s.kind.value in {"model_call", "tool_call"}]
    assert len(linked) == 4
    for step in linked:
        assert step.span_id in spans
        attributes = spans[step.span_id].attributes
        assert attributes is not None
        assert attributes["incidentops.run_id"] == step.run_id
    names = sorted(s.name for s in spans.values())
    assert names == ["chat", "chat", "tool get_consumer_status", "tool get_kafka_consumer_lag"]


def test_no_exporter_means_no_ids() -> None:
    with trace.get_tracer("noop").start_as_current_span("x") as span:
        assert telemetry.span_ids(span) == (None, None)


def test_configure_is_noop_without_endpoint() -> None:
    telemetry.configure("svc", environ={})
