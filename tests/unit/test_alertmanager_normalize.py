"""Unit tests for Alertmanager webhook normalization."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

import pytest

from incidentops.ingestion.alertmanager import (
    MAX_ALERTS_PER_PAYLOAD,
    PayloadRejectedError,
    dedup_key,
    normalize,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def payload(**alert_overrides: Any) -> dict[str, Any]:
    alert: dict[str, Any] = {
        "status": "firing",
        "labels": {
            "alertname": "lab_kafka_consumer_lag_high",
            "service_id": "persist-demo-consumer",
            "environment": "fault-lab",
            "severity": "warning",
            "component": "kafka-consumer",
        },
        "annotations": {"summary": "lag high"},
        "startsAt": "2026-11-09T11:58:00Z",
        "endsAt": "0001-01-01T00:00:00Z",
        "generatorURL": "http://prometheus/graph",
        "fingerprint": "a1b2c3d4e5f60718",
    }
    alert.update(alert_overrides)
    return {
        "version": "4",
        "groupKey": "{}:{}",
        "status": "firing",
        "receiver": "incidentops",
        "groupLabels": {},
        "commonLabels": {},
        "externalURL": "http://alertmanager",
        "alerts": [alert],
    }


def test_normalizes_firing_alert() -> None:
    (event,) = normalize(payload(), received_at=NOW)
    assert event.alert_name == "lab_kafka_consumer_lag_high"
    assert event.environment.value == "fault-lab"
    assert event.ends_at is None
    assert event.source_event_id == "a1b2c3d4e5f60718:2026-11-09T11:58:00+00:00"
    assert dedup_key(event) == "fault-lab:persist-demo-consumer:lab_kafka_consumer_lag_high"


def test_resolved_alert_keeps_end_time() -> None:
    (event,) = normalize(payload(status="resolved", endsAt="2026-11-09T12:01:00Z"), received_at=NOW)
    assert event.status.value == "resolved"
    assert event.ends_at == datetime(2026, 11, 9, 12, 1, tzinfo=UTC)


def test_digest_is_stable_across_deliveries() -> None:
    (first,) = normalize(payload(), received_at=NOW)
    (second,) = normalize(payload(), received_at=NOW)
    assert first.payload_digest == second.payload_digest
    assert first.id != second.id


@pytest.mark.parametrize("missing", ["alertname", "service_id", "environment", "severity"])
def test_missing_required_label_rejects_payload(missing: str) -> None:
    body = payload()
    del body["alerts"][0]["labels"][missing]
    with pytest.raises(PayloadRejectedError, match=missing):
        normalize(body, received_at=NOW)


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("environment", "mars"),
        ("severity", "apocalyptic"),
        ("service_id", "http://evil.example"),
        ("alertname", "DROP TABLE incidents"),
    ],
)
def test_invalid_label_values_reject_payload(label: str, value: str) -> None:
    body = payload()
    body["alerts"][0]["labels"][label] = value
    with pytest.raises(PayloadRejectedError):
        normalize(body, received_at=NOW)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda b: b.update(version="3"),
        lambda b: b.update(alerts=[]),
        lambda b: b["alerts"][0].update(fingerprint="not hex"),
        lambda b: b["alerts"][0].update(startsAt="yesterday"),
        lambda b: b.pop("alerts"),
    ],
)
def test_structurally_invalid_payloads_rejected(mutate: Any) -> None:
    body = payload()
    mutate(body)
    with pytest.raises(PayloadRejectedError):
        normalize(body, received_at=NOW)


def test_one_bad_alert_rejects_whole_payload() -> None:
    body = payload()
    bad = copy.deepcopy(body["alerts"][0])
    bad["labels"]["environment"] = "production-ish"
    body["alerts"].append(bad)
    with pytest.raises(PayloadRejectedError, match="alert 1"):
        normalize(body, received_at=NOW)


def test_alert_count_is_bounded() -> None:
    body = payload()
    body["alerts"] = body["alerts"] * (MAX_ALERTS_PER_PAYLOAD + 1)
    with pytest.raises(PayloadRejectedError):
        normalize(body, received_at=NOW)


def test_oversized_label_values_rejected() -> None:
    body = payload()
    body["alerts"][0]["labels"]["component"] = "x" * 513
    with pytest.raises(PayloadRejectedError):
        normalize(body, received_at=NOW)
