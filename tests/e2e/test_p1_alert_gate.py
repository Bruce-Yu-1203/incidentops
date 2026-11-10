"""Roadmap P1 gate against the running lab, observability stack and API.

Prerequisites: ``make lab-up``, ``make infra-up`` and ``make api-run``.
Each injected fault must travel through real telemetry: Prometheus rule ->
Alertmanager webhook -> exactly one active incident per service key.
"""

from __future__ import annotations

import os
import time
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fault_lab.scripts.labctl import LabController, LabEndpoints

from tests.e2e.session import API, sign_in_viewer

pytestmark = pytest.mark.lab

ALERT_TIMEOUT_S = 180
QUIET_TIMEOUT_S = 300


@pytest.fixture
def env() -> Iterator[tuple[LabController, httpx.Client]]:
    token = os.environ.get("FAULT_LAB_BENCHMARK_TOKEN", "")
    client = httpx.Client(timeout=10)
    try:
        client.get(f"{API}/healthz").raise_for_status()
        client.get("http://127.0.0.1:9093/-/ready").raise_for_status()
    except httpx.HTTPError:
        client.close()
        if os.environ.get("INCIDENTOPS_REQUIRE_LAB") == "1":
            pytest.fail("API and observability stack are required but unreachable")
        pytest.skip("run `make lab-up infra-up` and `make api-run` first")
    if not token:
        client.close()
        pytest.skip("FAULT_LAB_BENCHMARK_TOKEN not set")
    sign_in_viewer(client)
    try:
        yield LabController(LabEndpoints(), token, client), client
    finally:
        client.close()


def _active_incidents(client: httpx.Client) -> list[dict[str, Any]]:
    response = client.get(f"{API}/api/v1/incidents", params={"limit": 200})
    response.raise_for_status()
    terminal = {"DIAGNOSED_ONLY", "RESOLVED", "ESCALATED", "FAILED", "CANCELLED"}
    return [i for i in response.json() if i["state"] not in terminal]


def _wait_for_alert_event(
    client: httpx.Client, service_id: str, alert_name: str, since: datetime
) -> dict[str, Any]:
    deadline = time.monotonic() + ALERT_TIMEOUT_S
    while time.monotonic() < deadline:
        for incident in _active_incidents(client):
            if incident["service_id"] != service_id:
                continue
            detail = client.get(f"{API}/api/v1/incidents/{incident['id']}").json()
            for event in detail["alert_events"]:
                received = datetime.fromisoformat(event["received_at"])
                if event["alert_name"] == alert_name and received >= since:
                    return dict(detail)
        time.sleep(5)
    raise AssertionError(f"no {alert_name} alert reached an incident within {ALERT_TIMEOUT_S}s")


def _wait_for_quiet_baseline(client: httpx.Client) -> None:
    """Pretrial check (benchmark.md §2.2): no lab alert may still be firing.

    A still-firing alert would not be re-notified by Alertmanager, so a new
    injection could never be attributed to this trial.
    """
    deadline = time.monotonic() + QUIET_TIMEOUT_S
    while time.monotonic() < deadline:
        alerts = client.get("http://127.0.0.1:9093/api/v2/alerts").json()
        if not [a for a in alerts if a["labels"].get("environment") == "fault-lab"]:
            return
        time.sleep(5)
    raise AssertionError("lab alerts still firing; baseline not healthy")


def _assert_one_active_per_key(client: httpx.Client) -> None:
    keys = Counter(i["dedup_key"] for i in _active_incidents(client))
    assert all(count == 1 for count in keys.values()), keys


def test_kafka_pause_and_db_slowdown_each_open_one_incident(
    env: tuple[LabController, httpx.Client],
) -> None:
    lab, client = env
    lab.wait_ready()
    lab.reset()
    _wait_for_quiet_baseline(client)
    try:
        started = datetime.now(UTC)
        lab.pause()
        incident = _wait_for_alert_event(
            client, "persist-demo-consumer", "lab_kafka_consumer_lag_high", started
        )
        assert incident["dedup_key"] == "fault-lab:persist-demo-consumer"
        _assert_one_active_per_key(client)

        started = datetime.now(UTC)
        lab.inject_db_lock(hold_seconds=150)
        incident = _wait_for_alert_event(client, "lab-api", "lab_api_latency_p95_high", started)
        assert incident["dedup_key"] == "fault-lab:lab-api"
        _assert_one_active_per_key(client)
    finally:
        lab.reset()
