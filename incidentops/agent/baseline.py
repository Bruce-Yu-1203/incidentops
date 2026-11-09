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
        if service == "lab-api":
            return self._api_rules(results, evidence)
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
        if service == "lab-api":
            return ModelTurn(
                tool_calls=[
                    ToolCall(
                        "errors",
                        "query_metrics",
                        {
                            "template_id": "api_error_ratio",
                            "service_id": "lab-api",
                            "lookback_minutes": 5,
                        },
                    ),
                    ToolCall("pg", "get_postgres_health", {"service_id": "lab-postgres"}),
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

    def _api_rules(self, results: dict[str, dict[str, Any]], evidence: list[str]) -> ModelTurn:
        pg = results.get("pg", {}).get("summary", "")
        if "accessexclusivelock" in pg:
            return _report(
                "postgres_lock_contention",
                "lab-postgres",
                "medium",
                "An exclusive lock is held while API errors rise.",
                evidence,
            )
        return _report(
            "insufficient_evidence",
            "lab-api",
            "low",
            "API symptoms without a matching dependency signal.",
            evidence,
        )
