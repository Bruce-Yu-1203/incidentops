"""Tool gateway pipeline: validation, scope, budgets, evidence and trajectory."""

from __future__ import annotations

import json
from typing import Any

import pytest

from incidentops.security.identities import Role
from incidentops.tools.gateway import ToolStatus
from incidentops.tools.specs import TOOL_SPECS
from tests.unit.tool_fakes import FakeBackend, make_context, make_gateway


async def test_successful_metric_query_creates_evidence_and_step() -> None:
    gateway, backend, evidence, trajectory = make_gateway()
    ctx = make_context()
    result = await gateway.invoke(
        ctx, "tc1", "query_metrics", {"template_id": "api_latency_p95", "service_id": "lab-api"}
    )
    assert result.status is ToolStatus.SUCCESS
    assert result.evidence_id in evidence.items
    stored = evidence.items[result.evidence_id or ""]
    assert stored.query.template_id == "api_latency_p95"
    assert stored.digest.startswith("sha256:")
    assert backend.queries == [
        "histogram_quantile(0.95, sum by (le) "
        '(rate(lab_api_http_request_duration_seconds_bucket{service_id="lab-api"}[1m])))'
    ]
    (step,) = trajectory.steps
    assert (step.kind.value, step.status) == ("tool_call", "SUCCESS")


@pytest.mark.parametrize(
    ("tool", "args", "status", "code"),
    [
        ("run_shell", {"command": "rm -rf /"}, ToolStatus.DENIED, "tool_not_registered"),
        ("resume_test_consumer", {}, ToolStatus.DENIED, "tool_not_registered"),
        (
            "query_metrics",
            {"template_id": "api_latency_p95", "service_id": "lab-api", "url": "http://evil"},
            ToolStatus.INVALID,
            "invalid_arguments",
        ),
        (
            "query_metrics",
            {"template_id": "api_latency_p95", "service_id": "10.0.0.1"},
            ToolStatus.INVALID,
            "invalid_arguments",
        ),
        (
            "query_metrics",
            {"template_id": "api_latency_p95", "service_id": "prod-db"},
            ToolStatus.DENIED,
            "unregistered_target",
        ),
        (
            "query_metrics",
            {"template_id": "sum_everything", "service_id": "lab-api"},
            ToolStatus.DENIED,
            "out_of_scope",
        ),
        (
            "query_metrics",
            {"template_id": "kafka_group_lag", "service_id": "lab-api"},
            ToolStatus.DENIED,
            "out_of_scope",
        ),
        (
            "query_metrics",
            {"template_id": "api_latency_p95", "service_id": "lab-api", "lookback_minutes": 600},
            ToolStatus.INVALID,
            "invalid_arguments",
        ),
        (
            "get_postgres_health",
            {"service_id": "lab-api"},
            ToolStatus.DENIED,
            "tool_not_applicable",
        ),
        (
            "get_kafka_consumer_lag",
            {"consumer_group_id": "other-group"},
            ToolStatus.DENIED,
            "out_of_scope",
        ),
        (
            "search_logs",
            {"service_id": "lab-api", "contains": '" } |= "x'},
            ToolStatus.DENIED,
            "out_of_scope",
        ),
        ("query_metrics", "not a dict", ToolStatus.INVALID, "invalid_arguments"),
    ],
)
async def test_fail_closed_cases_are_recorded(
    tool: str, args: Any, status: ToolStatus, code: str
) -> None:
    # S-02 / S-08: unregistered tools, URLs, IPs and off-scope targets never execute.
    gateway, backend, evidence, trajectory = make_gateway()
    result = await gateway.invoke(make_context(), "tc", tool, args)
    assert (result.status, result.error_code) == (status, code)
    assert result.evidence_id is None
    assert evidence.items == {}
    assert len(trajectory.steps) == 1
    assert trajectory.steps[0].status == status.value
    if status is not ToolStatus.DENIED or code != "out_of_scope":
        assert backend.queries == []


async def test_non_agent_principal_without_read_capability_is_denied() -> None:
    gateway, backend, _, _ = make_gateway()
    result = await gateway.invoke(
        make_context(principal=Role.EXECUTOR),
        "tc",
        "query_metrics",
        {"template_id": "api_latency_p95", "service_id": "lab-api"},
    )
    assert result.error_code == "principal_not_authorized"
    assert backend.queries == []


async def test_per_tool_budget_is_enforced() -> None:
    gateway, _, _, trajectory = make_gateway()
    ctx = make_context()
    limit = TOOL_SPECS["get_consumer_status"].max_calls_per_run
    results = [
        await gateway.invoke(
            ctx, f"tc{i}", "get_consumer_status", {"worker_id": "persist-demo-consumer"}
        )
        for i in range(limit + 2)
    ]
    statuses = [r.status for r in results]
    assert statuses[:limit] == [ToolStatus.SUCCESS] * limit
    assert statuses[limit:] == [ToolStatus.BUDGET_EXCEEDED] * 2
    assert len(trajectory.steps) == limit + 2


async def test_run_budget_is_enforced() -> None:
    # S-12: flooding stops at the run budget and stays visible.
    gateway, _, _, _ = make_gateway()
    ctx = make_context(max_tool_calls=3)
    statuses = [
        (
            await gateway.invoke(
                ctx,
                f"tc{i}",
                "query_metrics",
                {"template_id": "api_error_ratio", "service_id": "lab-api"},
            )
        ).status
        for i in range(5)
    ]
    assert statuses == [ToolStatus.SUCCESS] * 3 + [ToolStatus.BUDGET_EXCEEDED] * 2


async def test_timeout_is_reported() -> None:
    backend = FakeBackend()
    backend.delay = 5
    gateway, _, evidence, _ = make_gateway(backend)
    result = await gateway.invoke(
        make_context(), "tc", "get_consumer_status", {"worker_id": "persist-demo-consumer"}
    )
    assert result.status is ToolStatus.TIMEOUT
    assert evidence.items == {}


async def test_backend_error_does_not_leak_details() -> None:
    backend = FakeBackend()
    backend.fail = True
    gateway, _, _, trajectory = make_gateway(backend)
    result = await gateway.invoke(
        make_context(),
        "tc",
        "query_metrics",
        {"template_id": "api_error_ratio", "service_id": "lab-api"},
    )
    assert result.status is ToolStatus.ERROR
    assert "abc" not in result.summary
    assert "prometheus:9090" not in json.dumps(trajectory.steps[0].metadata)


async def test_logs_are_untrusted_and_redacted() -> None:
    # S-07 / S-11: injected log text is returned only as data, with secrets removed.
    gateway, _, evidence, _ = make_gateway()
    result = await gateway.invoke(
        make_context(), "tc", "search_logs", {"service_id": "lab-api", "contains": "ERROR"}
    )
    payload = result.for_model()
    assert "untrusted_data" in payload
    assert "hunter2" not in payload["untrusted_data"]
    assert "hunter2" not in evidence.items[result.evidence_id or ""].snapshot


async def test_consumer_status_keeps_only_documented_fields() -> None:
    gateway, _, evidence, _ = make_gateway()
    result = await gateway.invoke(
        make_context(), "tc", "get_consumer_status", {"worker_id": "persist-demo-consumer"}
    )
    assert "should-not-pass" not in (result.data or "")
    assert "paused=True" in result.summary
    assert evidence.items[result.evidence_id or ""].source.value == "worker_status"


async def test_lag_tool_reports_trend() -> None:
    gateway, _, _, _ = make_gateway()
    result = await gateway.invoke(
        make_context(),
        "tc",
        "get_kafka_consumer_lag",
        {"consumer_group_id": "persist-demo-consumer"},
    )
    assert "100 -> 1300 (increasing)" in result.summary


async def test_postgres_health_and_traces() -> None:
    gateway, backend, _, _ = make_gateway()
    pg = await gateway.invoke(
        make_context(), "tc1", "get_postgres_health", {"service_id": "lab-postgres"}
    )
    assert "accessexclusivelock" in pg.summary
    traces = await gateway.invoke(
        make_context(),
        "tc2",
        "search_traces",
        {"service_id": "lab-api", "min_duration_ms": 500, "errors_only": True},
    )
    assert "slowest 5012 ms" in traces.summary
    assert backend.queries[-1] == (
        '{ resource.service.name="lab-api" && duration > 500ms && status = error }'
    )


def test_manifest_exposes_no_endpoints_or_write_tools() -> None:
    gateway, _, _, _ = make_gateway()
    manifest = json.dumps(gateway.manifest())
    assert "http" not in manifest
    names = {tool["name"] for tool in gateway.manifest()}
    assert names == set(TOOL_SPECS)
    assert not {n for n in names if any(w in n for w in ("resume", "pause", "restart", "shell"))}


def test_manifest_lists_templates_and_applicable_services() -> None:
    # A live DeepSeek run guessed template names (cpu_usage, broker_health) and
    # called search_logs on the consumer; all were denied. The manifest now
    # states the valid choices explicitly.
    gateway, _, _, _ = make_gateway()
    tools = {t["name"]: t for t in gateway.manifest()}
    query = tools["query_metrics"]
    assert "kafka_broker_count" in query["input_schema"]["properties"]["template_id"]["enum"]
    assert "producer_send_rate (messages/s; services: demo-producer)" in query["description"]
    assert tools["search_logs"]["description"].endswith("Applies to: lab-api.")
    assert "persist-demo-consumer" in tools["get_consumer_status"]["description"]


async def test_recent_deployments_tool_reports_release_changes() -> None:
    gateway, backend, evidence, _ = make_gateway()
    backend.deployments = {
        "service_id": "lab-api",
        "current_release": "rel-2",
        "pinned_healthy_release": "rel-1",
        "history": [{"release_id": "rel-2", "deployed_at": "2026-11-10T12:00:00Z", "actor": "x"}],
    }
    result = await gateway.invoke(
        make_context(), "d", "get_recent_deployments", {"service_id": "lab-api"}
    )
    assert result.status is ToolStatus.SUCCESS
    assert "runs rel-2" in result.summary
    stored = evidence.items[result.evidence_id or ""]
    assert stored.source.value == "deployment_controller"
    assert "actor" not in stored.snapshot  # only release IDs and times are kept
    denied = await gateway.invoke(
        make_context(), "e", "get_recent_deployments", {"service_id": "lab-redis"}
    )
    assert denied.error_code == "tool_not_applicable"
