"""Read tools never look back before the incident's history floor (ADR-029)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from incidentops.agent.graph import Budgets, build_graph, initial_state
from incidentops.agent.model import ModelTurn, ScriptedModelAdapter, ToolCall
from incidentops.agent.prompt import initial_message
from incidentops.config import Settings
from incidentops.domain.catalog import load_catalog
from incidentops.domain.ids import new_id
from incidentops.tools.gateway import ToolStatus
from incidentops.workers.investigation import budgets_from
from tests.unit.tool_fakes import NOW, make_context, make_gateway


async def _metric(floor_minutes_ago: float | None, lookback: int) -> Any:
    gateway, _, evidence, _ = make_gateway()
    floor = None if floor_minutes_ago is None else NOW - timedelta(minutes=floor_minutes_ago)
    result = await gateway.invoke(
        make_context(history_floor=floor),
        "tc1",
        "query_metrics",
        {"template_id": "api_latency_p95", "service_id": "lab-api", "lookback_minutes": lookback},
    )
    assert result.status is ToolStatus.SUCCESS
    return evidence.items[result.evidence_id or ""]


async def test_long_lookback_is_clamped_to_the_floor_and_says_so() -> None:
    stored = await _metric(floor_minutes_ago=7, lookback=30)
    assert stored.query.parameters["lookback_minutes"] == 7
    assert stored.window_start == NOW - timedelta(minutes=7)
    assert "[window limited to the last 7 min" in stored.summary


async def test_short_lookback_is_unchanged() -> None:
    stored = await _metric(floor_minutes_ago=7, lookback=5)
    assert stored.query.parameters["lookback_minutes"] == 5
    assert "window limited" not in stored.summary


async def test_no_floor_means_no_limit() -> None:
    stored = await _metric(floor_minutes_ago=None, lookback=30)
    assert stored.query.parameters["lookback_minutes"] == 30


async def test_floor_in_the_last_minute_still_reads_one_minute() -> None:
    stored = await _metric(floor_minutes_ago=0.2, lookback=15)
    assert stored.query.parameters["lookback_minutes"] == 1


def test_settings_default_to_a_five_minute_pre_window() -> None:
    settings = Settings(model_id="m")
    assert budgets_from(settings).history_pre_window_minutes == 5
    disabled = Settings(model_id="m", history_pre_window_minutes=0)
    assert budgets_from(disabled).history_pre_window_minutes is None


async def test_graph_passes_the_floor_to_every_tool_call() -> None:
    gateway, _, evidence, trajectory = make_gateway()
    call = ToolCall(
        "t1",
        "query_metrics",
        {"template_id": "api_latency_p95", "service_id": "lab-api", "lookback_minutes": 60},
    )
    model = ScriptedModelAdapter([ModelTurn(tool_calls=[call])])
    budgets = Budgets(history_pre_window_minutes=5)
    graph = build_graph(model, gateway, evidence, trajectory, budgets)
    incident_id = new_id("inc")
    state = initial_state(
        incident_id,
        new_id("run"),
        1,
        initial_message({"id": incident_id, "service_id": "lab-api"}, [], load_catalog()),
        budgets,
        history_floor_epoch_s=(NOW - timedelta(minutes=5)).timestamp(),
    )
    await graph.ainvoke(state)
    (stored,) = evidence.items.values()
    assert stored.query.parameters["lookback_minutes"] == 5


def test_floor_is_the_pre_window_before_the_incident_opened() -> None:
    from incidentops.agent.run_manager import InvestigationRunner

    runner = object.__new__(InvestigationRunner)
    runner._budgets = Budgets(history_pre_window_minutes=5)
    opened = NOW.isoformat()
    assert runner._history_floor({"created_at": opened}) == (NOW - timedelta(minutes=5)).timestamp()
    runner._budgets = Budgets()
    assert runner._history_floor({"created_at": opened}) is None
