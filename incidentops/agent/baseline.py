"""B0 rules-only baseline (benchmark.md §10) behind the ModelAdapter interface.

It uses exactly the same read tools, gateway, evidence and validation as the
LLM agent, but decides with fixed rules. It needs no provider key, which also
makes the closed loop runnable in local labs and CI. It is a baseline, not an
LLM, and is recorded as model ``rules-baseline-v1``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

from incidentops.agent.model import ModelTurn, ToolCall

BASELINE_MODEL_ID: Final = "rules-baseline-v1"
_SERVICE_ID = re.compile(r'"service_id":\s*"([a-z0-9-]+)"')


def _tool_results(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map tool_use_id -> parsed tool result body."""
    results: dict[str, dict[str, Any]] = {}
    for message in messages:
        for block in message.get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                results[block["tool_use_id"]] = json.loads(block["content"])
    return results


def _incident_service(messages: list[dict[str, Any]]) -> str | None:
    text = messages[0]["content"][0]["text"]
    marker = text.find("<untrusted_alert_data>")
    match = _SERVICE_ID.search(text[marker:]) if marker >= 0 else None
    return match.group(1) if match else None


def _report(
    root_cause: str,
    component: str,
    confidence: str,
    summary: str,
    evidence: list[str],
    action: dict[str, Any] | None = None,
) -> ModelTurn:
    payload: dict[str, Any] = {
        "diagnosis": {
            "root_cause_id": root_cause,
            "affected_component": component,
            "confidence_level": confidence,
            "summary": summary,
            "supporting_evidence_ids": evidence,
        },
        "needs_human_review": action is None,
    }
    if action is not None:
        payload["proposed_action"] = {**action, "evidence_ids": evidence}
    return ModelTurn(tool_calls=[ToolCall("final", "submit_diagnosis", payload)])


class RulesBaselineAdapter:
    """Deterministic rules over the same evidence the agent would see."""

    def __init__(self, consumer_group: str = "persist-demo-consumer") -> None:
        self._group = consumer_group

    @property
    def model_id(self) -> str:
        return BASELINE_MODEL_ID

    async def next_turn(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ModelTurn:
        results = _tool_results(messages)
        service = _incident_service(messages) or "unknown"
        if not results:
            return self._first_reads(service)
        evidence = [r["evidence_id"] for r in results.values() if r.get("evidence_id")]
        if service == self._group:
            return self._consumer_rules(results, evidence)
        if service in {"lab-api", "lab-redis"}:
            return self._api_rules(results, evidence, service)
        return _report("insufficient_evidence", service, "low", "No rules for service.", evidence)

    def _first_reads(self, service: str) -> ModelTurn:
        if service == self._group:
            return ModelTurn(
                tool_calls=[
                    ToolCall(
                        "lag",
                        "get_kafka_consumer_lag",
                        {"consumer_group_id": self._group, "lookback_minutes": 5},
                    ),
                    ToolCall("status", "get_consumer_status", {"worker_id": self._group}),
                    ToolCall(
                        "brokers",
                        "query_metrics",
                        {
                            "template_id": "kafka_broker_count",
                            "service_id": "lab-kafka",
                            "lookback_minutes": 5,
                        },
                    ),
                    ToolCall(
                        "producer",
                        "query_metrics",
                        {
                            "template_id": "producer_send_rate",
                            "service_id": "demo-producer",
                            "lookback_minutes": 5,
                        },
                    ),
                ]
            )
        if service in {"lab-api", "lab-redis"}:
            # The API and its dependencies are read together: one fault on a
            # dependency usually surfaces as API symptoms too.
            def metric(call_id: str, template: str, target: str) -> ToolCall:
                return ToolCall(
                    call_id,
                    "query_metrics",
                    {"template_id": template, "service_id": target, "lookback_minutes": 5},
                )

            return ModelTurn(
                tool_calls=[
                    metric("errors", "api_error_ratio", "lab-api"),
                    metric("latency", "api_latency_p95", "lab-api"),
                    metric("cpu", "api_cpu_cores", "lab-api"),
                    metric("memory", "api_memory_bytes", "lab-api"),
                    metric("memory_growth", "api_memory_growth_rate", "lab-api"),
                    metric("redis", "redis_up", "lab-redis"),
                    ToolCall("pg", "get_postgres_health", {"service_id": "lab-postgres"}),
                    ToolCall("deploys", "get_recent_deployments", {"service_id": "lab-api"}),
                ]
            )
        return ModelTurn(tool_calls=[])

    def _consumer_rules(self, results: dict[str, dict[str, Any]], evidence: list[str]) -> ModelTurn:
        lag = results.get("lag", {}).get("summary", "")
        status = results.get("status", {}).get("summary", "")
        brokers = results.get("brokers", {}).get("summary", "")
        lag_rising = "(increasing)" in lag
        paused = "paused=True" in status
        broker_up = "last=1.0" in brokers or "max=1.0" in brokers
        if lag_rising and paused and broker_up:
            return _report(
                "consumer_paused",
                self._group,
                "high",
                "Group lag rising while the worker reports paused and the broker is up.",
                evidence,
                {
                    "action_type": "resume_test_consumer",
                    "environment": "fault-lab",
                    "target_id": self._group,
                    "arguments": {"desired_paused": False},
                    "reason": "Resume the registered paused test consumer.",
                },
            )
        if lag_rising and not broker_up:
            return _report(
                "kafka_broker_unavailable",
                "lab-kafka",
                "medium",
                "Lag rising without a visible broker.",
                evidence,
            )
        return _report(
            "insufficient_evidence",
            self._group,
            "low",
            "Lag/status signals do not match a known rule.",
            evidence,
        )

    def _api_rules(
        self, results: dict[str, dict[str, Any]], evidence: list[str], service: str
    ) -> ModelTurn:
        """Fixed precedence: dependency faults first, then resources, then releases."""
        pg = results.get("pg", {}).get("summary", "")
        redis_last = _last(results.get("redis", {}).get("summary", ""))
        cpu_last = _last(results.get("cpu", {}).get("summary", ""))
        errors_last = _last(results.get("errors", {}).get("summary", ""))
        memory = results.get("memory", {}).get("summary", "")
        memory_growth = (_last(memory) or 0.0) - (_first(memory) or 0.0)
        # Peak growth rate in the lookback: a leak that already hit a cap (or a
        # window that still holds an earlier peak) shows no level increase but
        # still shows the growth burst (B-F04 evidence gap).
        growth_rate_max = _max(results.get("memory_growth", {}).get("summary", "")) or 0.0
        leaking = memory_growth > MEMORY_GROWTH_BYTES or growth_rate_max > MEMORY_GROWTH_RATE
        deploys = _data(results.get("deploys", {}))
        new_release = bool(
            deploys
            and deploys.get("current_release")
            and deploys.get("current_release") != deploys.get("pinned_healthy_release")
        )
        pinned = str(deploys.get("pinned_healthy_release") or "rel-1")

        if "accessexclusivelock" in pg:
            return _report(
                "postgres_lock_contention",
                "lab-postgres",
                "medium",
                "An exclusive lock is held on the lab database while API requests fail.",
                evidence,
                _action(
                    "release_lab_db_blocker",
                    "lab-postgres",
                    {},
                    "Release the known blocking lab transaction.",
                ),
            )
        if redis_last == 0.0:
            return _report(
                "redis_unavailable",
                "lab-redis",
                "high",
                "The lab Redis exporter reports Redis down while cache-backed requests fail.",
                evidence,
                _action(
                    "restart_lab_dependency",
                    "lab-redis",
                    {},
                    "Restart the registered lab Redis dependency.",
                ),
            )
        if cpu_last is not None and cpu_last > CPU_SATURATED_CORES:
            return _report(
                "cpu_saturation",
                "lab-api",
                "medium",
                f"lab-api uses {cpu_last:.2f} CPU cores.",
                evidence,
                _action(
                    "stop_lab_cpu_load",
                    "lab-cpu-load",
                    {},
                    "Stop the registered background load generator.",
                ),
            )
        if new_release and leaking:
            return _report(
                "application_memory_leak",
                "lab-api",
                "medium",
                f"Memory grew up to {growth_rate_max / 1e6:.1f} MB/s "
                f"({memory_growth / 1e6:.0f} MB in the window) after release "
                f"{deploys.get('current_release')}.",
                evidence,
                _action(
                    "rollback_demo_api",
                    "lab-api",
                    {"target_release": pinned},
                    "Roll back to the pinned healthy release.",
                ),
            )
        if new_release and errors_last is not None and errors_last > ERROR_RATIO:
            return _report(
                "bad_deployment",
                "lab-api",
                "medium",
                f"Errors at {errors_last:.0%} after release {deploys.get('current_release')}.",
                evidence,
                _action(
                    "rollback_demo_api",
                    "lab-api",
                    {"target_release": pinned},
                    "Roll back to the pinned healthy release.",
                ),
            )
        return _report(
            "insufficient_evidence",
            service,
            "low",
            "Symptoms without a matching dependency, resource or release signal.",
            evidence,
        )


CPU_SATURATED_CORES: Final = 0.6
MEMORY_GROWTH_BYTES: Final = 30_000_000
# Sustained leaks in the lab grow by MB/s; normal request churn stays far below.
MEMORY_GROWTH_RATE: Final = 500_000
ERROR_RATIO: Final = 0.05
_LAST = re.compile(r"last=([-0-9.e+]+)")
_FIRST = re.compile(r"first=([-0-9.e+]+)")
_MAX = re.compile(r"max=([-0-9.e+]+)")


def _last(summary: str) -> float | None:
    match = _LAST.search(summary)
    return float(match.group(1)) if match else None


def _first(summary: str) -> float | None:
    match = _FIRST.search(summary)
    return float(match.group(1)) if match else None


def _max(summary: str) -> float | None:
    match = _MAX.search(summary)
    return float(match.group(1)) if match else None


def _data(result: dict[str, Any]) -> dict[str, Any]:
    raw = result.get("untrusted_data")
    try:
        value = json.loads(raw) if isinstance(raw, str) else None
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _action(
    action_type: str, target_id: str, arguments: dict[str, Any], reason: str
) -> dict[str, Any]:
    return {
        "action_type": action_type,
        "environment": "fault-lab",
        "target_id": target_id,
        "arguments": arguments,
        "reason": reason,
    }
