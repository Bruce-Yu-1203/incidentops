"""B0 rules baseline through the real graph, gateway and validation."""

from __future__ import annotations

from typing import Any

import pytest

from incidentops.agent.baseline import BASELINE_MODEL_ID, RulesBaselineAdapter
from incidentops.agent.graph import Budgets, build_graph, initial_state
from incidentops.agent.prompt import initial_message
from incidentops.domain.catalog import load_catalog
from incidentops.domain.ids import new_id
from tests.unit.tool_fakes import FakeBackend, make_gateway


async def _run(service_id: str, backend: FakeBackend) -> dict[str, Any]:
    gateway, _, evidence, trajectory = make_gateway(backend)
    graph = build_graph(RulesBaselineAdapter(), gateway, evidence, trajectory, Budgets())
    incident_id = new_id("inc")
    result: dict[str, Any] = await graph.ainvoke(
        initial_state(
            incident_id,
            new_id("run"),
            1,
            initial_message({"id": incident_id, "service_id": service_id}, [], load_catalog()),
            Budgets(),
        )
    )
    return result


async def test_paused_consumer_yields_resume_proposal() -> None:
    final = await _run("persist-demo-consumer", FakeBackend())
    assert final["status"] == "COMPLETED"
    report = final["report"]
    assert report["diagnosis"]["root_cause_id"] == "consumer_paused"
    assert report["proposed_action"]["action_type"] == "resume_test_consumer"
    assert set(report["diagnosis"]["supporting_evidence_ids"]) == set(final["evidence_ids"])


@pytest.mark.parametrize(
    ("paused", "brokers", "root_cause"),
    [
        (False, 1.0, "insufficient_evidence"),  # e.g. slow downstream, not paused (B-N02)
        (True, 0.0, "kafka_broker_unavailable"),
    ],
)
async def test_non_matching_signals_do_not_propose_resume(
    paused: bool, brokers: float, root_cause: str
) -> None:
    backend = FakeBackend()
    backend.paused = paused
    backend.brokers = brokers
    report = (await _run("persist-demo-consumer", backend))["report"]
    assert report["diagnosis"]["root_cause_id"] == root_cause
    assert report.get("proposed_action") is None
    assert report["needs_human_review"] is True


async def test_api_incident_with_exclusive_lock() -> None:
    report = (await _run("lab-api", FakeBackend()))["report"]
    assert report["diagnosis"]["root_cause_id"] == "postgres_lock_contention"
    assert report.get("proposed_action") is None


def test_baseline_identifies_itself() -> None:
    assert RulesBaselineAdapter().model_id == BASELINE_MODEL_ID
