"""Transactional incident upsert with outbox scheduling (FR-002, FR-004).

For each normalized alert, in one database transaction:

1. find the active incident for the dedup key (row-locked), or insert a new
   one guarded by the partial unique index (concurrent deliveries race safely);
2. append the alert event (repeated deliveries are always retained);
3. for a newly created incident, append an audit event and enqueue exactly
   one ``investigate_incident`` outbox job.

Resolved alerts never change incident state: only the independent verifier
may resolve an incident.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from incidentops.domain.disposition import HANDOVER_STATES
from incidentops.domain.ids import new_id
from incidentops.domain.schemas import AlertEvent, AlertStatus
from incidentops.domain.state_machines import INCIDENT_MACHINE, IncidentState
from incidentops.ingestion.alertmanager import dedup_key
from incidentops.persistence.models import AlertEventRow, AuditEventRow, IncidentRow, OutboxJobRow

INVESTIGATE_JOB = "investigate_incident"
_ACTIVE_PREDICATE = "state NOT IN ({})".format(
    ", ".join(f"'{s.value}'" for s in sorted(INCIDENT_MACHINE.terminal_states))
)


@dataclass
class IngestResult:
    created_incident_ids: list[str] = field(default_factory=list)
    updated_incident_ids: list[str] = field(default_factory=list)
    ignored_resolved: int = 0


def _active_incident(session: Session, key: str) -> IncidentRow | None:
    terminal = [s.value for s in INCIDENT_MACHINE.terminal_states]
    return session.scalars(
        select(IncidentRow)
        .where(IncidentRow.dedup_key == key, IncidentRow.state.not_in(terminal))
        .with_for_update()
    ).one_or_none()


def _waiting_handover(session: Session, key: str) -> IncidentRow | None:
    """The newest handed-over incident for ``key`` that still waits for a person."""
    return session.scalars(
        select(IncidentRow)
        .where(
            IncidentRow.dedup_key == key,
            IncidentRow.state.in_([s.value for s in HANDOVER_STATES]),
            IncidentRow.disposition.is_(None),
        )
        .order_by(IncidentRow.created_at.desc())
        .limit(1)
    ).first()


def _create_incident(session: Session, event: AlertEvent, key: str) -> str | None:
    """Insert a new incident; return ``None`` if a concurrent insert won."""
    incident_id = new_id("inc")
    statement = (
        insert(IncidentRow)
        .values(
            id=incident_id,
            dedup_key=key,
            service_id=event.service_id,
            environment=event.environment.value,
            severity=event.severity.value,
            state=IncidentState.RECEIVED.value,
            version=1,
        )
        .on_conflict_do_nothing(index_elements=["dedup_key"], index_where=text(_ACTIVE_PREDICATE))
        .returning(IncidentRow.id)
    )
    return session.execute(statement).scalar_one_or_none()


def _record_event(session: Session, incident_id: str, event: AlertEvent) -> None:
    session.add(
        AlertEventRow(
            id=event.id,
            incident_id=incident_id,
            source_event_id=event.source_event_id,
            fingerprint=event.fingerprint,
            status=event.status.value,
            payload=event.model_dump(mode="json"),
            payload_digest=event.payload_digest,
            received_at=event.received_at,
        )
    )


def ingest_events(
    factory: sessionmaker[Session], events: list[AlertEvent], actor: str
) -> IngestResult:
    """Persist all events of one webhook delivery in a single transaction."""
    result = IngestResult()
    # A stable key order keeps row-lock acquisition consistent across
    # concurrent deliveries and prevents deadlocks.
    ordered = sorted(events, key=dedup_key)
    with factory.begin() as session:
        for event in ordered:
            key = dedup_key(event)
            incident = _active_incident(session, key)
            if incident is not None:
                _record_event(session, incident.id, event)
                result.updated_incident_ids.append(incident.id)
                continue
            if event.status is AlertStatus.RESOLVED:
                # A resolved notification alone must not open an incident. It
                # is kept on a handed-over incident that still waits for a
                # person, as a hint only: the state never changes (ADR-021).
                waiting = _waiting_handover(session, key)
                if waiting is None:
                    result.ignored_resolved += 1
                else:
                    _record_event(session, waiting.id, event)
                    result.updated_incident_ids.append(waiting.id)
                continue

            created_id = _create_incident(session, event, key)
            if created_id is None:
                # Lost the race: another transaction created it first. Its
                # row is now visible after the conflict wait, so lock and reuse.
                winner = _active_incident(session, key)
                if winner is None:  # pragma: no cover - would violate the index
                    raise RuntimeError(f"dedup race left no active incident for {key}")
                _record_event(session, winner.id, event)
                result.updated_incident_ids.append(winner.id)
                continue

            _record_event(session, created_id, event)
            session.add(
                AuditEventRow(
                    id=new_id("aud"),
                    actor=actor,
                    event_type="incident_created",
                    resource_type="incidents",
                    resource_id=created_id,
                    decision=IncidentState.RECEIVED.value,
                    reason=f"alert {event.alert_name} firing",
                    correlation_ids={"alert_event_id": event.id, "dedup_key": key},
                )
            )
            session.add(
                OutboxJobRow(
                    id=new_id("job"),
                    kind=INVESTIGATE_JOB,
                    aggregate_id=created_id,
                    dedup_key=f"{INVESTIGATE_JOB}:{created_id}",
                    payload={"incident_id": created_id, "incident_version": 1},
                )
            )
            result.created_incident_ids.append(created_id)
    return result
