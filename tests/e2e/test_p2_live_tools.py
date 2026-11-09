"""P2 live check: the read tools and graph against real lab telemetry.

Uses a scripted model (no provider key needed) so the check isolates the tool
gateway, templates and backends from model variability. Requires lab-up and
infra-up, plus the FAULT_LAB_* tokens from .env.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fault_lab.scripts.labctl import LabController, LabEndpoints

from incidentops.agent.graph import Budgets, build_graph, initial_state
from incidentops.agent.model import (
    ModelTurn,
    ScriptedModelAdapter,
    ScriptStep,
    ToolCall,
    evidence_ids_in,
)
from incidentops.agent.prompt import initial_message
from incidentops.domain.catalog import load_catalog
from incidentops.domain.ids import new_id
from incidentops.evidence.store import InMemoryEvidenceSink
from incidentops.tools.backends import HttpTelemetryBackend
from incidentops.tools.gateway import RunContext, ToolGateway, ToolStatus
from incidentops.trajectory.recorder import InMemoryTrajectorySink

pytestmark = pytest.mark.lab

CONSUMER = "persist-demo-consumer"


@pytest.fixture
async def live() -> AsyncIterator[tuple[ToolGateway, LabController, InMemoryTrajectorySink]]:
    token = os.environ.get("FAULT_LAB_BENCHMARK_TOKEN", "")
    # Same value as INCIDENTOPS_LAB_READ_TOKEN, which the hermetic settings
    # fixture removes from the environment.
    read_token = os.environ.get("FAULT_LAB_READ_TOKEN", "")
    sync = httpx.Client(timeout=10)
    try:
        sync.get("http://127.0.0.1:9090/-/ready").raise_for_status()
        sync.get(f"{LabEndpoints().consumer}/healthz").raise_for_status()
    except httpx.HTTPError:
        sync.close()
        if os.environ.get("INCIDENTOPS_REQUIRE_LAB") == "1":
            pytest.fail("lab and observability stack are required")
        pytest.skip("run `make lab-up infra-up` first")
    if not (token and read_token):
        sync.close()
        if os.environ.get("INCIDENTOPS_REQUIRE_LAB") == "1":
            pytest.fail("FAULT_LAB_BENCHMARK_TOKEN and FAULT_LAB_READ_TOKEN are required")
        pytest.skip("lab tokens not set")
    lab = LabController(LabEndpoints(), token, sync)
    async with httpx.AsyncClient(timeout=10) as client:
        backend = HttpTelemetryBackend(
            client,
            "http://127.0.0.1:9090",
            "http://127.0.0.1:3100",
            "http://127.0.0.1:3200",
            read_token,
            {CONSUMER: LabEndpoints().consumer},
        )
        trajectory = InMemoryTrajectorySink()
        gateway = ToolGateway(load_catalog(), backend, InMemoryEvidenceSink(), trajectory)
        try:
            lab.reset()
            yield gateway, lab, trajectory
        finally:
            lab.reset()
            sync.close()


def live_evidence(gateway: ToolGateway) -> InMemoryEvidenceSink:
    sink = gateway._evidence
    assert isinstance(sink, InMemoryEvidenceSink)
    return sink


async def _wait_for(query: str, threshold: float, timeout_s: float = 150) -> None:
    deadline = time.monotonic() + timeout_s
    async with httpx.AsyncClient(timeout=5) as client:
        while time.monotonic() < deadline:
            body = (
                await client.get("http://127.0.0.1:9090/api/v1/query", params={"query": query})
            ).json()
            result = body["data"]["result"]
            if result and float(result[0]["value"][1]) > threshold:
                return
            await asyncio.sleep(5)
    raise AssertionError(f"{query} never exceeded {threshold}")


async def test_paused_consumer_is_visible_through_tools_and_graph(
    live: tuple[ToolGateway, LabController, InMemoryTrajectorySink],
) -> None:
    gateway, lab, trajectory = live
    lab.pause()
    await _wait_for("lab_kafka:consumergroup_lag:sum", 300)

    turns: list[ScriptStep] = [
        ModelTurn(
            tool_calls=[
                ToolCall(
                    "t1",
                    "get_kafka_consumer_lag",
                    {"consumer_group_id": CONSUMER, "lookback_minutes": 5},
                ),
                ToolCall("t2", "get_consumer_status", {"worker_id": CONSUMER}),
                ToolCall(
                    "t3",
                    "query_metrics",
                    {"template_id": "producer_send_rate", "service_id": "demo-producer"},
                ),
                ToolCall(
                    "t4",
                    "query_metrics",
                    {"template_id": "kafka_broker_count", "service_id": "lab-kafka"},
                ),
            ]
        ),
        lambda messages: ModelTurn(
            tool_calls=[
                ToolCall(
                    "final",
                    "submit_diagnosis",
                    {
                        "diagnosis": {
                            "root_cause_id": "consumer_paused",
                            "affected_component": CONSUMER,
                            "confidence_level": "high",
                            "summary": "Live check",
                            "supporting_evidence_ids": evidence_ids_in(messages),
                        },
                        "needs_human_review": True,
                    },
                )
            ]
        ),
    ]
    model = ScriptedModelAdapter(turns)
    graph = build_graph(model, gateway, live_evidence(gateway), trajectory, Budgets())
    incident_id = new_id("inc")
    final: dict[str, Any] = await graph.ainvoke(
        initial_state(
            incident_id,
            new_id("run"),
            1,
            initial_message({"id": incident_id, "service_id": CONSUMER}, [], load_catalog()),
            Budgets(),
        )
    )
    assert final["status"] == "COMPLETED", final.get("error_code")
    summaries = [s.metadata["summary"] for s in trajectory.steps if s.kind.value == "tool_call"]
    assert all(s.status == "SUCCESS" for s in trajectory.steps if s.kind.value == "tool_call")
    assert "(increasing)" in summaries[0]
    assert "paused=True" in summaries[1]
    assert "no data" not in summaries[2]  # producer still sending
    assert "last=1.0" in summaries[3]  # one healthy broker


async def test_db_lock_is_visible_in_postgres_logs_and_traces(
    live: tuple[ToolGateway, LabController, InMemoryTrajectorySink],
) -> None:
    gateway, lab, _ = live
    lab.inject_db_lock(hold_seconds=120)
    await _wait_for('sum(pg_locks_count{mode="accessexclusivelock"})', 0)
    await _wait_for("lab_api:error_ratio_1m", 0.05)
    ctx = RunContext(incident_id=new_id("inc"), run_id=new_id("run"))

    pg = await gateway.invoke(ctx, "a", "get_postgres_health", {"service_id": "lab-postgres"})
    assert pg.status is ToolStatus.SUCCESS
    assert "accessexclusivelock" in pg.summary

    errors = await gateway.invoke(
        ctx,
        "b",
        "query_metrics",
        {"template_id": "api_error_ratio", "service_id": "lab-api", "lookback_minutes": 5},
    )
    assert errors.status is ToolStatus.SUCCESS

    logs = await gateway.invoke(
        ctx,
        "c",
        "search_logs",
        {"service_id": "lab-api", "contains": "item query failed", "lookback_minutes": 5},
    )
    assert logs.status is ToolStatus.SUCCESS
    assert not logs.summary.startswith("0 log lines")

    traces = await gateway.invoke(
        ctx,
        "d",
        "search_traces",
        {"service_id": "lab-api", "min_duration_ms": 1000, "lookback_minutes": 5},
    )
    assert traces.status is ToolStatus.SUCCESS
    assert not traces.summary.startswith("0 traces")
