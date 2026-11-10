"""Tests for the demo API, producer and benchmark CLI helpers."""

from __future__ import annotations

import random
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fault_lab.api.app import create_app as create_api
from fault_lab.loadgen.app import create_app as create_loadgen
from fault_lab.loadgen.app import item_path
from fault_lab.producer.app import create_app as create_producer
from fault_lab.producer.app import make_event
from fault_lab.scripts.labctl import main as labctl_main
from fault_lab.scripts.labctl import parse_metric


class FakeDeps:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy
        self.prepared = False
        self.cache_down = False

    async def prepare(self) -> None:
        self.prepared = True

    async def touch_cache(self) -> None:
        if self.cache_down:
            raise ConnectionError("redis paused")

    async def get_item(self, item_id: int) -> dict[str, Any] | None:
        if not self.healthy:
            raise TimeoutError("statement timeout")
        return {"id": item_id, "name": f"item-{item_id}"} if item_id <= 10 else None

    async def check_database(self) -> None:
        if not self.healthy:
            raise ConnectionError("db down")

    async def check_cache(self) -> None:
        return None

    async def count_events(self) -> int:
        if not self.healthy:
            raise ConnectionError("db down")
        return 7


def test_api_ready_and_instrumented() -> None:
    deps = FakeDeps()
    with TestClient(create_api(deps)) as client:
        assert deps.prepared
    client = TestClient(create_api(deps))
    assert client.get("/readyz").json() == {"status": "ready"}
    assert client.get("/events/count").json() == {"count": 7}
    metrics = client.get("/metrics").text
    assert 'lab_api_http_requests_total{route="/readyz",status="200"} 1.0' in metrics
    assert "lab_api_http_request_duration_seconds_bucket" in metrics


def test_api_reports_dependency_failure_as_503() -> None:
    client = TestClient(create_api(FakeDeps(healthy=False)))
    assert client.get("/readyz").status_code == 503
    assert client.get("/events/count").status_code == 503
    assert 'status="503"' in client.get("/metrics").text


def test_producer_events_are_deterministic_per_seed() -> None:
    first = [make_event(random.Random(1042), i) for i in range(1)]  # noqa: S311
    second = [make_event(random.Random(1042), i) for i in range(1)]  # noqa: S311
    assert first == second
    assert first[0]["sequence"] == 0


def test_producer_exposes_target_rate() -> None:
    with TestClient(create_producer(rate_per_second=20, seed=1, run_loop=False)) as client:
        assert "lab_producer_target_rate 20.0" in client.get("/metrics").text


def test_parse_metric_reads_unlabelled_sample() -> None:
    text = '# HELP x\nlab_consumer_messages_processed_total 12.0\nother{a="b"} 3\n'
    assert parse_metric(text, "lab_consumer_messages_processed_total") == 12.0
    with pytest.raises(KeyError):
        parse_metric(text, "missing")


def test_labctl_requires_benchmark_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FAULT_LAB_BENCHMARK_TOKEN", raising=False)
    assert labctl_main(["status"]) == 2


def test_items_route_reads_database_and_validates_ids() -> None:
    client = TestClient(create_api(FakeDeps()))
    assert client.get("/items/3").json() == {"id": 3, "name": "item-3"}
    assert client.get("/items/11").status_code == 404
    assert client.get("/items/0").status_code == 422
    assert client.get("/items/1001").status_code == 422
    metrics = client.get("/metrics").text
    assert 'lab_api_http_requests_total{route="/items/{item_id}",status="200"} 1.0' in metrics


def test_items_route_maps_database_failure_to_503() -> None:
    client = TestClient(create_api(FakeDeps(healthy=False)))
    assert client.get("/items/1").status_code == 503
    assert 'route="/items/{item_id}",status="503"' in client.get("/metrics").text


def test_loadgen_paths_are_seeded_and_in_range() -> None:
    first = [item_path(random.Random(7)) for _ in range(3)]  # noqa: S311
    second = [item_path(random.Random(7)) for _ in range(3)]  # noqa: S311
    assert first == second
    assert all(1 <= int(p.rsplit("/", 1)[1]) <= 1000 for p in first)


def test_loadgen_exposes_metrics() -> None:
    app = create_loadgen("http://lab-api:8080", rate_per_second=5, seed=1, run_loop=False)
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/metrics").status_code == 200
