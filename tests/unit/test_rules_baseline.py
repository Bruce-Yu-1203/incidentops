"""B0 rules baseline through the real graph, gateway and validation."""

from __future__ import annotations

from typing import Any

import pytest

from incidentops.agent.baseline import BASELINE_MODEL_ID, RulesBaselineAdapter
from incidentops.agent.graph import Budgets, build_graph, initial_state
from incidentops.agent.prompt import initial_message
from incidentops.domain.catalog import load_catalog
from incidentops.domain.ids import new_id
from tests.unit.tool_fakes import NOW, FakeBackend, make_gateway


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


def _kafka_backend(
    *,
    paused: bool,
    brokers: float,
    lag: tuple[float, float] = (100.0, 1300.0),
    **series: list[float],
) -> FakeBackend:
    backend = FakeBackend()
    backend.paused = paused
    backend.brokers = brokers
    backend.lag = [(NOW.timestamp() - 60, lag[0]), (NOW.timestamp(), lag[1])]
    backend.overrides = {
        "lab_producer_messages_sent_total": [20.0, 20.0],
        "lab_producer_send_errors_total": [0.0, 0.0],
        "lab_consumer_messages_processed_total": [20.0, 20.0],
    }
    backend.overrides.update(series)
    return backend


@pytest.mark.parametrize(
    ("service", "backend", "root_cause", "component", "action"),
    [
        # B-F03: paused worker, broker up -> resume.
        (
            "persist-demo-consumer",
            _kafka_backend(
                paused=True, brokers=1.0, lab_consumer_messages_processed_total=[20.0, 0.0]
            ),
            "consumer_paused",
            "persist-demo-consumer",
            "resume_test_consumer",
        ),
        # B-F07: broker gone for every client -> restart the broker, whichever
        # service raised the incident.
        (
            "lab-kafka",
            _kafka_backend(
                paused=False,
                brokers=0.0,
                lag=(0.0, 0.0),
                lab_producer_messages_sent_total=[20.0, 0.0],
                lab_producer_send_errors_total=[0.0, 0.2],
            ),
            "kafka_broker_unavailable",
            "lab-kafka",
            "restart_lab_dependency",
        ),
        (
            "persist-demo-consumer",
            _kafka_backend(paused=True, brokers=0.0, lab_producer_send_errors_total=[0.0, 0.2]),
            "kafka_broker_unavailable",
            "lab-kafka",
            "restart_lab_dependency",
        ),
        # B-F08: running but slow consumer -> consumer_slow, never resume.
        (
            "persist-demo-consumer",
            _kafka_backend(
                paused=False, brokers=1.0, lab_consumer_messages_processed_total=[20.0, 10.0]
            ),
            "consumer_slow",
            "persist-demo-consumer",
            None,
        ),
        # A consumer that is neither paused nor processing (e.g. its database
        # is down, B-F09) matches no consumer rule.
        (
            "persist-demo-consumer",
            _kafka_backend(
                paused=False, brokers=1.0, lab_consumer_messages_processed_total=[20.0, 0.0]
            ),
            "insufficient_evidence",
            "persist-demo-consumer",
            None,
        ),
        # Broker count dropped but the producer still sends: not enough for
        # a broker diagnosis (for example an exporter restart).
        (
            "lab-kafka",
            _kafka_backend(paused=False, brokers=0.0, lag=(10.0, 10.0)),
            "insufficient_evidence",
            "lab-kafka",
            None,
        ),
    ],
)
async def test_kafka_family_rules(
    service: str, backend: FakeBackend, root_cause: str, component: str, action: str | None
) -> None:
    report = (await _run(service, backend))["report"]
    assert report["diagnosis"]["root_cause_id"] == root_cause
    assert report["diagnosis"]["affected_component"] == component
    proposed = report.get("proposed_action")
    if action is None:
        assert proposed is None
        assert report["needs_human_review"] is True
    else:
        assert proposed["action_type"] == action
        assert proposed["target_id"] == component


async def test_api_incident_with_exclusive_lock() -> None:
    report = (await _run("lab-api", FakeBackend()))["report"]
    assert report["diagnosis"]["root_cause_id"] == "postgres_lock_contention"
    assert report["proposed_action"]["action_type"] == "release_lab_db_blocker"
    assert report["proposed_action"]["target_id"] == "lab-postgres"


def _api_backend(**overrides: list[float]) -> FakeBackend:
    backend = FakeBackend()
    backend.db_locked = False
    backend.overrides = {
        "redis_up": [1, 1, 1],
        "process_cpu_seconds_total": [0.05, 0.05],
        "process_resident_memory_bytes": [1.0e8, 1.0e8],
        "deriv(process_resident_memory_bytes": [0.0, 0.0],
        "lab_api_http_requests_total": [0.0, 0.0],
    }
    backend.overrides.update(overrides)
    return backend


def _tracebacks(backend: FakeBackend, count: int = 5) -> FakeBackend:
    backend.log_lines = [
        "unhandled exception serving item 4\nTraceback (most recent call last):\n"
        "KeyError: 'pricing'"
    ] * count
    return backend


def _pg_down(backend: FakeBackend) -> FakeBackend:
    backend.instant_overrides = {"pg_up": 0.0}
    return backend


def _deployed(backend: FakeBackend) -> FakeBackend:
    backend.deployments = {
        "service_id": "lab-api",
        "current_release": "rel-2",
        "pinned_healthy_release": "rel-1",
        "history": [{"release_id": "rel-2", "deployed_at": "2026-11-10T12:00:00Z"}],
    }
    return backend


@pytest.mark.parametrize(
    ("service", "backend", "root_cause", "action", "target"),
    [
        (
            "lab-redis",
            _api_backend(redis_up=[1, 0, 0]),
            "redis_unavailable",
            "restart_lab_dependency",
            "lab-redis",
        ),
        (
            "lab-api",
            _api_backend(redis_up=[1, 0, 0]),
            "redis_unavailable",
            "restart_lab_dependency",
            "lab-redis",
        ),
        (
            "lab-api",
            _api_backend(process_cpu_seconds_total=[0.1, 0.9]),
            "cpu_saturation",
            "stop_lab_cpu_load",
            "lab-cpu-load",
        ),
        (
            "lab-api",
            _deployed(_api_backend(process_resident_memory_bytes=[1.0e8, 3.0e8])),
            "application_memory_leak",
            "rollback_demo_api",
            "lab-api",
        ),
        # B-F04 after the leak capped: memory is flat or lower than an earlier
        # peak in the window, but the growth burst is still in the lookback.
        (
            "lab-api",
            _deployed(
                _api_backend(
                    process_resident_memory_bytes=[4.17e8, 3.97e8],
                    **{"deriv(process_resident_memory_bytes": [2.5e6, 2.4e6, 0.0, -1.0e5]},
                )
            ),
            "application_memory_leak",
            "rollback_demo_api",
            "lab-api",
        ),
        # A new release whose memory merely fluctuates is not a leak.
        (
            "lab-api",
            _deployed(
                _api_backend(
                    lab_api_http_requests_total=[0.3, 0.3],
                    **{"deriv(process_resident_memory_bytes": [2.0e4, -3.0e4, 1.0e4]},
                )
            ),
            "bad_deployment",
            "rollback_demo_api",
            "lab-api",
        ),
        (
            "lab-api",
            _deployed(_api_backend(lab_api_http_requests_total=[0.3, 0.3])),
            "bad_deployment",
            "rollback_demo_api",
            "lab-api",
        ),
        (
            "lab-api",
            _api_backend(lab_api_http_requests_total=[0.2, 0.08]),
            "insufficient_evidence",
            None,
            None,
        ),  # B-N01: errors without a cause
        # B-F09: the database refuses connections -> restart it, from either
        # the API's or the database's incident; never release a lock blocker.
        (
            "lab-api",
            _pg_down(_api_backend(lab_api_http_requests_total=[0.5, 0.5])),
            "postgres_unavailable",
            "restart_lab_dependency",
            "lab-postgres",
        ),
        (
            "lab-postgres",
            _pg_down(_api_backend(lab_api_http_requests_total=[0.5, 0.5])),
            "postgres_unavailable",
            "restart_lab_dependency",
            "lab-postgres",
        ),
        # B-F10: errors with stack traces in the running release -> no action.
        (
            "lab-api",
            _tracebacks(_api_backend(lab_api_http_requests_total=[0.25, 0.25])),
            "application_error",
            None,
            None,
        ),
        # The same errors after a new release stay a bad deployment (B-F05).
        (
            "lab-api",
            _tracebacks(_deployed(_api_backend(lab_api_http_requests_total=[0.3, 0.3]))),
            "bad_deployment",
            "rollback_demo_api",
            "lab-api",
        ),
        # A couple of stack traces among transient errors are not enough (B-N01).
        (
            "lab-api",
            _tracebacks(_api_backend(lab_api_http_requests_total=[0.2, 0.08]), count=2),
            "insufficient_evidence",
            None,
            None,
        ),
    ],
)
async def test_api_family_rules(
    service: str, backend: FakeBackend, root_cause: str, action: str | None, target: str | None
) -> None:
    report = (await _run(service, backend))["report"]
    assert report["diagnosis"]["root_cause_id"] == root_cause
    proposed = report.get("proposed_action")
    if action is None:
        assert proposed is None
    else:
        assert (proposed["action_type"], proposed["target_id"]) == (action, target)
        if action == "rollback_demo_api":
            assert proposed["arguments"] == {"target_release": "rel-1"}


def test_baseline_identifies_itself() -> None:
    assert RulesBaselineAdapter().model_id == BASELINE_MODEL_ID
