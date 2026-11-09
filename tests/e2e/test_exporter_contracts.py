"""Live contract: metric names and labels that alert rules and query templates rely on.

Exporter naming differs between versions (tech-stack §5.1), so the pinned
versions are verified against a running Prometheus. Requires lab-up + infra-up.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import httpx
import pytest

pytestmark = pytest.mark.lab

PROMETHEUS = os.environ.get("INCIDENTOPS_PROMETHEUS_URL", "http://127.0.0.1:9090")

# metric selector -> labels every returned series must carry
EXPECTED = {
    'kafka_consumergroup_lag{consumergroup="persist-demo-consumer"}': {
        "consumergroup",
        "topic",
        "partition",
        "service_id",
    },
    'lab_api_http_request_duration_seconds_bucket{route="/items/{item_id}"}': {"route", "le"},
    "lab_api_http_requests_total": {"route", "status"},
    "lab_consumer_paused": {"service_id"},
    "lab_consumer_messages_processed_total": {"service_id"},
    "lab_producer_messages_sent_total": {"service_id"},
    "pg_up": {"service_id"},
    "pg_stat_activity_count": {"state", "datname"},
    "redis_up": {"service_id"},
    "redis_memory_used_bytes": {"service_id"},
}


@pytest.fixture
def prometheus() -> Iterator[httpx.Client]:
    client = httpx.Client(base_url=PROMETHEUS, timeout=10)
    try:
        client.get("/-/ready").raise_for_status()
    except httpx.HTTPError:
        client.close()
        if os.environ.get("INCIDENTOPS_REQUIRE_LAB") == "1":
            pytest.fail("Prometheus is required but unreachable")
        pytest.skip("run `make lab-up infra-up` first")
    try:
        yield client
    finally:
        client.close()


@pytest.mark.parametrize(("selector", "labels"), EXPECTED.items(), ids=list(EXPECTED))
def test_metric_exists_with_labels(
    prometheus: httpx.Client, selector: str, labels: set[str]
) -> None:
    result = prometheus.get("/api/v1/query", params={"query": selector}).json()["data"]["result"]
    assert result, f"no series for {selector}"
    for series in result:
        missing = labels - set(series["metric"])
        assert not missing, f"{selector} missing labels {missing}"


def test_all_scrape_targets_are_healthy(prometheus: httpx.Client) -> None:
    targets = prometheus.get("/api/v1/targets").json()["data"]["activeTargets"]
    unhealthy = {t["labels"]["job"]: t["lastError"] for t in targets if t["health"] != "up"}
    assert not unhealthy


def test_fault_controller_state_is_not_in_prometheus(prometheus: httpx.Client) -> None:
    names = prometheus.get("/api/v1/label/__name__/values").json()["data"]
    leaked = [n for n in names if n.startswith("lab_fault") or "db_lock" in n or "fixture" in n]
    assert not leaked
