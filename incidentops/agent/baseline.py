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
        if service in KAFKA_FAMILY:
            return self._consumer_rules(results, evidence, service)
        if service in API_FAMILY:
            return self._api_rules(results, evidence, service)
        return _report("insufficient_evidence", service, "low", "No rules for service.", evidence)

    def _first_reads(self, service: str) -> ModelTurn:
        if service in KAFKA_FAMILY:
            # Consumer, broker and producer are read together so the broker
            # can be told apart from its healthy neighbours.
            return ModelTurn(
                tool_calls=[
                    ToolCall(
                        "lag",
                        "get_kafka_consumer_lag",
                        {"consumer_group_id": self._group, "lookback_minutes": 5},
                    ),
                    ToolCall("status", "get_consumer_status", {"worker_id": self._group}),
                    _metric("brokers", "kafka_broker_count", "lab-kafka"),
                    _metric("producer", "producer_send_rate", "demo-producer"),
                    _metric("producer_errors", "producer_send_error_rate", "demo-producer"),
                    _metric("processing", "consumer_processing_rate", self._group),
                ]
            )
        if service in API_FAMILY:
            # The API and its dependencies are read together: one fault on a
            # dependency usually surfaces as API symptoms too.
            return ModelTurn(
                tool_calls=[
                    _metric("errors", "api_error_ratio", "lab-api"),
                    _metric("latency", "api_latency_p95", "lab-api"),
                    _metric("cpu", "api_cpu_cores", "lab-api"),
                    _metric("memory", "api_memory_bytes", "lab-api"),
                    _metric("memory_growth", "api_memory_growth_rate", "lab-api"),
                    _metric("redis", "redis_up", "lab-redis"),
                    ToolCall("pg", "get_postgres_health", {"service_id": "lab-postgres"}),
                    ToolCall("deploys", "get_recent_deployments", {"service_id": "lab-api"}),
                    _metric("traffic", "api_request_rate_vs_baseline", "lab-api"),
                    ToolCall("config", "get_recent_config_changes", {"service_id": "lab-api"}),
                    ToolCall(
                        "tracebacks",
                        "search_logs",
                        {
                            "service_id": "lab-api",
                            "lookback_minutes": 5,
                            "contains": "Traceback",
                            "limit": 20,
                        },
                    ),
                ]
            )
        return ModelTurn(tool_calls=[])

    def _consumer_rules(
        self, results: dict[str, dict[str, Any]], evidence: list[str], service: str
    ) -> ModelTurn:
        """Fixed precedence: broker first, then the consumer's own state."""
        lag = results.get("lag", {}).get("summary", "")
        status = results.get("status", {}).get("summary", "")
        brokers_last = _last(results.get("brokers", {}).get("summary", ""))
        sent_last = _last(results.get("producer", {}).get("summary", ""))
        send_errors_last = _last(results.get("producer_errors", {}).get("summary", ""))
        processed_last = _last(results.get("processing", {}).get("summary", ""))
        lag_rising = "(increasing)" in lag
        paused = "paused=True" in status
        # Current visibility, not the window maximum: a broker that just
        # vanished still has earlier samples of 1 in the lookback.
        broker_up = brokers_last is not None and brokers_last >= 1
        producer_failing = (send_errors_last or 0.0) > 0 or (sent_last or 0.0) < 1
        if not broker_up and producer_failing:
            return _report(
                "kafka_broker_unavailable",
                "lab-kafka",
                "high",
                "The lag exporter sees no broker and the producer cannot send.",
                evidence,
                _action(
                    "restart_lab_dependency",
                    "lab-kafka",
                    {},
                    "Restart the registered lab Kafka broker.",
                ),
            )
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
        if lag_rising and not paused and broker_up and (processed_last or 0.0) > 0:
            return _report(
                "consumer_slow",
                self._group,
                "medium",
                f"Lag rising while the running consumer processes {processed_last:.1f} msg/s.",
                evidence,
            )
        return _report(
            "insufficient_evidence",
            self._group if service == self._group else service,
            "low",
            "Lag/status signals do not match a known rule.",
            evidence,
        )

    def _api_rules(
        self, results: dict[str, dict[str, Any]], evidence: list[str], service: str
    ) -> ModelTurn:
        """Fixed precedence: dependencies, then load and resources, then changes."""
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
        tracebacks = _log_count(results.get("tracebacks", {}).get("summary", ""))
        traffic_last = _last(results.get("traffic", {}).get("summary", ""))
        config = _data(results.get("config", {}))
        config_changed = bool(
            config
            and config.get("current_revision")
            and config.get("current_revision") != config.get("pinned_good_revision")
        )
        good_config = str(config.get("pinned_good_revision") or "cfg-1")
        pool = _CONNECTIONS.search(pg)
        utilization = (
            int(pool.group(1)) / int(pool.group(2)) if pool and int(pool.group(2)) else 0.0
        )

        if _POSTGRES_DOWN.search(pg):
            return _report(
                "postgres_unavailable",
                "lab-postgres",
                "high",
                "The lab PostgreSQL exporter reports the database down while requests fail.",
                evidence,
                _action(
                    "restart_lab_dependency",
                    "lab-postgres",
                    {},
                    "Restart the registered lab PostgreSQL dependency.",
                ),
            )
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
        if utilization >= POOL_SATURATED:
            return _report(
                "connection_pool_exhausted",
                "lab-postgres",
                "medium",
                f"lab-postgres uses {pool.group(1) if pool else '?'} of "
                f"{pool.group(2) if pool else '?'} connections while API requests fail.",
                evidence,
                _action(
                    "release_lab_idle_connections",
                    "lab-postgres",
                    {},
                    "Release the known idle connections holding the lab database slots.",
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
        if traffic_last is not None and traffic_last >= TRAFFIC_SURGE_FACTOR:
            # Checked before CPU: more requests also mean more CPU (B-F06 look-alike).
            return _report(
                "traffic_surge",
                "lab-api",
                "medium",
                f"lab-api receives {traffic_last:.1f}x its usual request rate.",
                evidence,
                _action(
                    "reset_lab_load_profile",
                    "lab-loadgen",
                    {},
                    "Restore the registered load generator to its baseline profile.",
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
        if config_changed and errors_last is not None and errors_last > ERROR_RATIO:
            # Errors after a runtime config change without a new release.
            return _report(
                "config_error",
                "lab-api",
                "medium",
                f"Errors at {errors_last:.0%} after config change to "
                f"{config.get('current_revision')}; the release is unchanged.",
                evidence,
                _action(
                    "revert_lab_config",
                    "lab-api-config",
                    {"target_config": good_config},
                    "Revert lab-api to its last known good configuration.",
                ),
            )
        if (
            not new_release
            and errors_last is not None
            and errors_last > ERROR_RATIO
            and tracebacks >= MIN_TRACEBACKS
        ):
            # Errors with stack traces in the running release, no deploy: a
            # code defect. Rolling back to the same release cannot fix it.
            return _report(
                "application_error",
                "lab-api",
                "medium",
                f"Errors at {errors_last:.0%} with {tracebacks} stack traces in release "
                f"{deploys.get('current_release')}; no recent deploy.",
                evidence,
            )
        return _report(
            "insufficient_evidence",
            service,
            "low",
            "Symptoms without a matching dependency, resource or release signal.",
            evidence,
        )


KAFKA_FAMILY: Final = frozenset({"persist-demo-consumer", "lab-kafka", "demo-producer"})
API_FAMILY: Final = frozenset({"lab-api", "lab-redis", "lab-postgres"})
MIN_TRACEBACKS: Final = 3
POOL_SATURATED: Final = 0.9
TRAFFIC_SURGE_FACTOR: Final = 3.0
_CONNECTIONS = re.compile(r"connections=(\d+)/(\d+)")
_POSTGRES_DOWN = re.compile(r"\bup=0(\.0)?\b")
_LOG_COUNT = re.compile(r"^(\d+) log lines")
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


def _log_count(summary: str) -> int:
    match = _LOG_COUNT.match(summary)
    return int(match.group(1)) if match else 0


def _metric(call_id: str, template: str, target: str) -> ToolCall:
    return ToolCall(
        call_id,
        "query_metrics",
        {"template_id": template, "service_id": target, "lookback_minutes": 5},
    )


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
