"""Alertmanager webhook (payload version 4) parsing and normalization.

Labels and annotations are untrusted input. Only a small set of required
labels is interpreted; everything else is stored as data.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from incidentops.domain.ids import new_id
from incidentops.domain.schemas import AlertEvent, AlertStatus, Environment, Severity

MAX_ALERTS_PER_PAYLOAD = 200
REQUIRED_LABELS = ("alertname", "service_id", "environment", "severity")
# Alertmanager uses the zero time for "no end time".
_ZERO_TIME = datetime(1, 1, 1, tzinfo=UTC)

LabelValue = Annotated[str, StringConstraints(max_length=512)]


class AlertmanagerAlert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["firing", "resolved"]
    labels: dict[str, LabelValue]
    annotations: dict[str, Annotated[str, StringConstraints(max_length=2000)]] = Field(
        default_factory=dict
    )
    startsAt: datetime  # noqa: N815 - Alertmanager wire format
    endsAt: datetime | None = None  # noqa: N815
    fingerprint: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{8,64}$")]


class AlertmanagerPayload(BaseModel):
    """Webhook body; unknown top-level fields are ignored for forward compatibility."""

    model_config = ConfigDict(extra="ignore")

    version: Literal["4"]
    status: Literal["firing", "resolved"]
    receiver: Annotated[str, StringConstraints(max_length=256)]
    alerts: list[AlertmanagerAlert] = Field(min_length=1, max_length=MAX_ALERTS_PER_PAYLOAD)


class PayloadRejectedError(ValueError):
    """The webhook payload is malformed or missing required labels."""


def dedup_key(event: AlertEvent) -> str:
    """One active incident per (environment, service) — ADR-017.

    Several alert rules firing for the same service (for example latency and
    error ratio during one database slowdown) correlate into one incident.
    """
    return f"{event.environment.value}:{event.service_id}"


def _digest(alert: AlertmanagerAlert) -> str:
    canonical = json.dumps(alert.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def normalize(raw: Any, received_at: datetime) -> list[AlertEvent]:
    """Validate a raw webhook body and return one ``AlertEvent`` per alert.

    The whole payload is rejected if any alert is malformed, so a partially
    understood delivery never creates a partial incident set.
    """
    try:
        payload = AlertmanagerPayload.model_validate(raw)
    except ValidationError as exc:
        raise PayloadRejectedError(f"invalid payload: {exc.error_count()} error(s)") from exc

    events: list[AlertEvent] = []
    for index, alert in enumerate(payload.alerts):
        missing = [name for name in REQUIRED_LABELS if not alert.labels.get(name)]
        if missing:
            raise PayloadRejectedError(f"alert {index} missing labels: {', '.join(missing)}")
        ends_at = alert.endsAt if alert.endsAt and alert.endsAt > _ZERO_TIME else None
        try:
            events.append(
                AlertEvent(
                    id=new_id("alert"),
                    source_event_id=f"{alert.fingerprint}:{alert.startsAt.isoformat()}",
                    fingerprint=alert.fingerprint,
                    alert_name=alert.labels["alertname"],
                    status=AlertStatus(alert.status),
                    service_id=alert.labels["service_id"],
                    environment=Environment(alert.labels["environment"]),
                    severity=Severity(alert.labels["severity"]),
                    labels=alert.labels,
                    annotations=alert.annotations,
                    starts_at=alert.startsAt,
                    ends_at=ends_at,
                    received_at=received_at,
                    payload_digest=_digest(alert),
                )
            )
        except (ValidationError, ValueError) as exc:
            raise PayloadRejectedError(f"alert {index} has invalid label values") from exc
    return events
