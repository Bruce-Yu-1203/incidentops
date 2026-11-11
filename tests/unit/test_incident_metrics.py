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
    assert incident_panel_id("lab-kafka", None) is None


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
