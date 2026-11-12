"""Tests for the demo API, producer and benchmark CLI helpers."""

from __future__ import annotations

import asyncio
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

    async def get_item(self, item_id: int, database: str = "lab") -> dict[str, Any] | None:
        if not self.healthy:
            raise TimeoutError("statement timeout")
        if database != "lab":
            raise ConnectionError(f'database "{database}" does not exist')
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


class _FlakyProducer:
    def __init__(self, fail: BaseException | None = None, hang: bool = False) -> None:
        self.fail = fail
        self.hang = hang
        self.sent: list[bytes] = []

    async def send_and_wait(self, topic: str, value: bytes) -> None:
        if self.hang:
            await asyncio.sleep(10)
        if self.fail is not None:
            raise self.fail
        self.sent.append(value)


@pytest.mark.parametrize(
    ("producer", "ok"),
    [
        (_FlakyProducer(), True),
        (_FlakyProducer(fail=ConnectionError("broker gone")), False),
        (_FlakyProducer(hang=True), False),  # an unreachable broker never answers
    ],
)
async def test_send_failures_are_counted_not_fatal(producer: _FlakyProducer, ok: bool) -> None:
    from fault_lab.producer.app import send_one
    from prometheus_client import CollectorRegistry, Counter

    registry = CollectorRegistry()
    sent = Counter("s", "sent", registry=registry)
    errors = Counter("e", "errors", registry=registry)
    event = make_event(random.Random(1), 0)  # noqa: S311 - deterministic test data
    assert await send_one(producer, "demo.events.v1", event, sent, errors, timeout_s=0.05) is ok
    assert registry.get_sample_value("s_total") == (1.0 if ok else 0.0)
    assert registry.get_sample_value("e_total") == (0.0 if ok else 1.0)


def test_producer_exposes_send_errors() -> None:
    with TestClient(create_producer(rate_per_second=20, seed=1, run_loop=False)) as client:
        assert "lab_producer_send_errors_total 0.0" in client.get("/metrics").text


def test_loadgen_profile_needs_the_internal_token_and_is_bounded() -> None:
    app = create_loadgen(
        "http://api",
        rate_per_second=10,
        seed=1,
        run_loop=False,
        internal_token="internal-token-0123456789",
    )
    client = TestClient(app)
    auth = {"Authorization": "Bearer internal-token-0123456789"}
    assert client.post("/internal/load-profile", json={"rate_per_second": 60}).status_code == 403
    assert client.post(
        "/internal/load-profile", json={"rate_per_second": 60}, headers=auth
    ).json() == {"rate_per_second": 60.0}
    assert (
        client.post(
            "/internal/load-profile", json={"rate_per_second": 5000}, headers=auth
        ).status_code
        == 422
    )
    assert client.post("/internal/load-profile", json={"reset": True}, headers=auth).json() == {
        "rate_per_second": 10.0
    }
    untokened = TestClient(create_loadgen("http://api", 10, 1, run_loop=False))
    assert (
        untokened.post("/internal/load-profile", json={"reset": True}, headers=auth).status_code
        == 403
    )
