"""Incident replay stays anchored to alert and terminal audit times."""

from datetime import timedelta

from incidentops.api.incidents import IncidentDetail
from incidentops.api.metrics import incident_metric_window, incident_panel_id
from incidentops.domain.state_machines import IncidentState
from tests.unit.tool_fakes import NOW


def test_replay_uses_terminal_audit_instead_of_later_manual_update() -> None:
    incident = IncidentDetail.model_validate(
        {
            "id": "inc_123456",
            "dedup_key": "lab-api",
            "service_id": "lab-api",
            "environment": "fault-lab",
            "severity": "warning",
            "state": "ESCALATED",
            "version": 4,
            "created_at": NOW,
            "updated_at": NOW + timedelta(days=1),
            "escalation_reason": "insufficient_evidence",
            "runs": [],
            "actions": [],
            "diagnosis": None,
            "alert_events": [
                {
                    "id": "alt_123456",
                    "status": "firing",
                    "alert_name": "cpu",
                    "fingerprint": "abc",
                    "starts_at": (NOW - timedelta(minutes=1)).isoformat(),
                    "received_at": NOW,
                    "labels": {},
                    "annotations": {},
                }
            ],
            "timeline": [
                {
                    "at": NOW + timedelta(minutes=2),
                    "kind": "audit",
                    "summary": "escalated",
                    "reference_id": "aud_123456",
                    "resource_type": "incidents",
                    "decision": "UNKNOWN",
                    "target_state": "ESCALATED",
                }
            ],
        }
    )
    start, end, markers = incident_metric_window(incident, NOW + timedelta(days=2))
    assert start == NOW - timedelta(minutes=6)
    assert end == NOW + timedelta(minutes=7)
    assert markers[-1].at == NOW + timedelta(minutes=2)
    incident.state = IncidentState.INVESTIGATING
    incident.timeline = []
    assert incident_metric_window(incident, NOW + timedelta(minutes=3))[1] == NOW + timedelta(
        minutes=3
    )


def test_alert_selects_explanatory_metric() -> None:
    assert incident_panel_id("lab-api", "lab_api_cpu_high") == "api_cpu"
    assert incident_panel_id("lab-api", "lab_api_memory_growth") == "api_memory_growth"
    assert incident_panel_id("lab-redis", "lab_redis_down") == "redis_up"
    assert incident_panel_id("lab-kafka", None) == "kafka_brokers"
    assert incident_panel_id("lab-postgres", "lab_postgres_down") == "pg_up"
    assert (
        incident_panel_id("lab-postgres", "lab_postgres_connections_near_limit") == "pg_pool_usage"
    )
    assert incident_panel_id("lab-api", "lab_api_request_rate_surge") == "api_request_rate"
    assert incident_panel_id("persist-demo-consumer", "lab_consumer_processing_slow") == (
        "produce_consume"
    )
    assert incident_panel_id("demo-producer", None) is None


async def test_empty_partial_and_redis_down_are_distinct() -> None:
    from incidentops.api.metrics import load_panels
    from incidentops.domain.catalog import load_catalog
    from incidentops.tools.backends import Series
    from tests.unit.tool_fakes import FakeBackend

    class EmptyBackend(FakeBackend):
        async def prometheus_range(
            self, query: str, start: object, end: object, step_seconds: int
        ) -> list[Series]:
            return []

    panels = await load_panels(EmptyBackend(), load_catalog(), NOW, NOW, 5, {"api_cpu"})
    assert panels[0].status == "no_data"

    backend = FakeBackend()
    backend.overrides = {"redis_up": [0]}
    panels = await load_panels(backend, load_catalog(), NOW, NOW, 5, {"redis_up"})
    assert panels[0].status == "over"
    assert panels[0].threshold_direction == "below"

    class PartialBackend(FakeBackend):
        async def prometheus_range(
            self, query: str, start: object, end: object, step_seconds: int
        ) -> list[Series]:
            if "processed" in query:
                raise ConnectionError("source unavailable")
            return [Series({}, [(NOW.timestamp(), 1)])]

    panels = await load_panels(PartialBackend(), load_catalog(), NOW, NOW, 5, {"produce_consume"})
    assert panels[0].status == "partial"


def test_every_alert_rule_maps_to_a_panel_and_every_panel_query_renders() -> None:
    from pathlib import Path

    import yaml

    from incidentops.api.metrics import PANELS
    from incidentops.domain.catalog import load_catalog

    rules = Path(__file__).resolve().parents[2] / "infra" / "prometheus" / "rules" / "lab.rules.yml"
    groups = yaml.safe_load(rules.read_text(encoding="utf-8"))["groups"]
    records = {r["record"] for g in groups for r in g["rules"] if "record" in r}
    panel_ids = {p.panel_id for p in PANELS}
    for group in groups:
        for rule in group["rules"]:
            if "alert" in rule:
                panel = incident_panel_id(rule["labels"]["service_id"], rule["alert"])
                assert panel in panel_ids, rule["alert"]
    catalog = load_catalog()
    for spec in PANELS:
        for query in spec.queries:
            promql = query.build(catalog)
            # Fixed queries name recording rules that exist.
            if ":" in promql and "(" not in promql:
                assert promql.removeprefix("sum(").removesuffix(")") in records, promql
