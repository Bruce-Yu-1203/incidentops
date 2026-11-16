"""Incident groups across related services (ADR-030).

One fault in a shared dependency makes several services alert, and each opens
its own incident (one per ``environment:service_id``, ADR-017). Grouping keeps
those incidents and adds a group on top: only the **primary**, the most
upstream member, is investigated; the other members are ``GROUPED`` and
follow it.

Rules, applied in the ingestion transaction under a per-environment advisory
lock so concurrent deliveries cannot form two groups for one fault:

* G1 window: a new incident joins a group only if a related service (a direct
  catalog edge in either direction, or co-location) has an active, unsplit
  incident in that group opened within the grouping window.
* G2 symptom class: an incident opened by an ``intrinsic`` alert never becomes
  a non-primary member. It starts its own group unless it becomes the primary.
  G2b: a later intrinsic alert on a GROUPED member splits it out at once.
* Primary: the most upstream member. A more upstream newcomer takes over if
  the current primary was opened by a dependency symptom and has not finished
  its investigation. A primary whose run had not started becomes GROUPED and
  its pending job is cancelled; a primary whose run is going finishes it, and
  the run's proposal is dropped (the run stays as context).
* Settle: a primary whose service has dependencies waits a settle time before
  its investigation, so a more upstream incident can still join first.

Members follow the primary when it reaches a terminal state (see
``on_incident_terminal``): RESOLVED hands the members to the verifier, which
resolves those whose alerts recovered and splits out the others (G4); a
handover state escalates them; a cancelled primary releases them. A person may
split a member out by hand (G5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from incidentops.domain.catalog import ServiceCatalog
from incidentops.domain.ids import new_id
from incidentops.domain.schemas import AlertStatus, SymptomClass
from incidentops.domain.state_machines import INCIDENT_MACHINE, IncidentState
from incidentops.persistence.models import (
    AlertEventRow,
    AuditEventRow,
    DiagnosisRow,
    IncidentGroupRow,
    IncidentRow,
    InvestigationRunRow,
    OutboxJobRow,
)

INVESTIGATE_JOB: Final = "investigate_incident"
VERIFY_GROUP_MEMBERS_JOB: Final = "verify_group_members"
DEFAULT_WINDOW_SECONDS: Final = 180
DEFAULT_SETTLE_SECONDS: Final = 60
SPLIT_BY_VERIFIER_NOTE: Final = "not explained by the group primary's fix"
# G2b: an intrinsic alert arrived on a member that joined on a dependency symptom.
INTRINSIC_ON_MEMBER: Final = "intrinsic_symptom_on_member"
INTRINSIC_ON_MEMBER_NOTE: Final = "an intrinsic symptom started on this service while grouped"
# A current primary in one of these states may still hand over to a more
# upstream newcomer; after DIAGNOSED its proposal may already be in flight.
_REPLACEABLE_PRIMARY: Final = frozenset(
    {IncidentState.RECEIVED, IncidentState.TRIAGED, IncidentState.INVESTIGATING}
)
_NOT_STARTED: Final = frozenset({IncidentState.RECEIVED, IncidentState.TRIAGED})
# Primary terminal states that hand its members over to a person with it.
_ESCALATING: Final = frozenset(
    {IncidentState.ESCALATED, IncidentState.DIAGNOSED_ONLY, IncidentState.FAILED}
)


@dataclass(frozen=True)
class GroupingConfig:
    catalog: ServiceCatalog
    window_seconds: int = DEFAULT_WINDOW_SECONDS
    settle_seconds: int = DEFAULT_SETTLE_SECONDS


@dataclass(frozen=True)
class GroupDecision:
    """Whether the new incident is investigated, and after how many seconds."""

    group_id: str
    investigate: bool
    delay_seconds: int = 0


def lock_environment(session: Session, environment: str) -> None:
    """Serialize grouping per environment until the transaction ends."""
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"incident_groups:{environment}"},
    )


def _audit(
    session: Session,
    actor: str,
    event_type: str,
    incident_id: str,
    reason: str,
    **ids: str,
) -> None:
    session.add(
        AuditEventRow(
            id=new_id("aud"),
            actor=actor,
            event_type=event_type,
            resource_type="incidents",
            resource_id=incident_id,
            decision=event_type.upper(),
            reason=reason,
            correlation_ids={"incident_id": incident_id, **ids},
        )
    )


def _rank(catalog: ServiceCatalog, service_id: str) -> int:
    # An unregistered service has no edges and is never more upstream.
    return catalog.upstream_rank(service_id) if catalog.is_registered(service_id) else 1 << 16


def _settle(config: GroupingConfig, service_id: str) -> int:
    catalog = config.catalog
    if catalog.is_registered(service_id) and catalog.get(service_id).depends_on:
        return config.settle_seconds
    return 0


def _open_group(
    session: Session, config: GroupingConfig, incident: IncidentRow, now: datetime
) -> GroupDecision:
    group_id = new_id("grp")
    session.add(
        IncidentGroupRow(
            id=group_id,
            environment=incident.environment,
            primary_incident_id=incident.id,
            opened_at=now,
            window_ends_at=now + timedelta(seconds=config.window_seconds),
        )
    )
    session.flush()
    session.execute(
        update(IncidentRow).where(IncidentRow.id == incident.id).values(group_id=group_id)
    )
    return GroupDecision(group_id, True, _settle(config, incident.service_id))


def _candidate_group(
    session: Session, config: GroupingConfig, incident: IncidentRow, now: datetime
) -> IncidentGroupRow | None:
    """The oldest group with an active, unsplit, related incident inside the window."""
    terminal = [s.value for s in INCIDENT_MACHINE.terminal_states]
    since = now - timedelta(seconds=config.window_seconds)
    rows = session.execute(
        select(IncidentRow.service_id, IncidentRow.group_id).where(
            IncidentRow.environment == incident.environment,
            IncidentRow.id != incident.id,
            IncidentRow.state.not_in(terminal),
            IncidentRow.group_id.is_not(None),
            IncidentRow.group_split_at.is_(None),
            IncidentRow.created_at >= since,
        )
    ).all()
    group_ids = {
        group_id
        for service_id, group_id in rows
        if config.catalog.related(incident.service_id, service_id)
    }
    if not group_ids:
        return None
    return session.scalars(
        select(IncidentGroupRow)
        .where(IncidentGroupRow.id.in_(sorted(group_ids)))
        .order_by(IncidentGroupRow.opened_at, IncidentGroupRow.id)
        .limit(1)
    ).first()


def _extend_window(
    session: Session, config: GroupingConfig, group: IncidentGroupRow, now: datetime
) -> None:
    ends = now + timedelta(seconds=config.window_seconds)
    session.execute(
        update(IncidentGroupRow)
        .where(IncidentGroupRow.id == group.id)
        .values(window_ends_at=func.greatest(IncidentGroupRow.window_ends_at, ends))
    )


def cancel_pending_investigation(session: Session, incident_id: str, reason: str) -> int:
    """Retire unclaimed investigation jobs of an incident (a leased one no-ops)."""
    result = session.execute(
        update(OutboxJobRow)
        .where(
            OutboxJobRow.kind == INVESTIGATE_JOB,
            OutboxJobRow.aggregate_id == incident_id,
            OutboxJobRow.status == "PENDING",
        )
        .values(status="DONE", last_error=f"cancelled: {reason}"[:500])
        .returning(OutboxJobRow.id)
    )
    return len(result.all())


def assign_group(
    session: Session,
    config: GroupingConfig,
    incident_id: str,
    symptom: SymptomClass,
    actor: str,
    now: datetime,
) -> GroupDecision:
    """Place a newly created incident in a group (caller holds the environment lock)."""
    from incidentops.persistence.transitions import AuditSpec, transition

    incident = session.get_one(IncidentRow, incident_id)
    catalog = config.catalog
    group = _candidate_group(session, config, incident, now)
    if group is None:
        return _open_group(session, config, incident, now)

    primary = session.get_one(IncidentRow, group.primary_incident_id, with_for_update=True)
    primary_state = IncidentState(primary.state)
    ids = {"group_id": group.id, "primary_incident_id": primary.id}

    if _rank(catalog, incident.service_id) < _rank(catalog, primary.service_id):
        if primary.symptom_class == SymptomClass.INTRINSIC.value:
            _audit(
                session, actor, "incident_not_grouped", incident.id,
                "the group primary was opened by an intrinsic symptom", **ids,
            )  # fmt: skip
            return _open_group(session, config, incident, now)
        if primary_state not in _REPLACEABLE_PRIMARY:
            _audit(
                session, actor, "incident_not_grouped", incident.id,
                f"the group primary is already {primary_state.value}", **ids,
            )  # fmt: skip
            return _open_group(session, config, incident, now)
        # The newcomer is more upstream: it becomes the primary.
        session.execute(
            update(IncidentGroupRow)
            .where(IncidentGroupRow.id == group.id)
            .values(primary_incident_id=incident.id)
        )
        session.execute(
            update(IncidentRow).where(IncidentRow.id == incident.id).values(group_id=group.id)
        )
        _extend_window(session, config, group, now)
        _audit(
            session, actor, "group_primary_changed", incident.id,
            f"{incident.service_id} is upstream of {primary.service_id}",
            group_id=group.id, previous_primary_id=primary.id,
        )  # fmt: skip
        if primary_state in _NOT_STARTED:
            transition(
                session,
                IncidentRow,
                primary.id,
                expected_state=primary_state,
                expected_version=primary.version,
                target_state=IncidentState.GROUPED,
                audit=AuditSpec(
                    actor=actor,
                    event_type="incident_grouped",
                    reason=f"a more upstream incident on {incident.service_id} joined",
                    correlation_ids={"group_id": group.id, "primary_incident_id": incident.id},
                ),
            )
            cancel_pending_investigation(session, primary.id, "grouped under a new primary")
        else:
            # Its run continues; its result is regrouped without a proposal.
            _audit(
                session, actor, "group_primary_replaced", primary.id,
                "investigation already running: it finishes as context without a proposal",
                group_id=group.id, primary_incident_id=incident.id,
            )  # fmt: skip
        return GroupDecision(group.id, True, _settle(config, incident.service_id))

    if symptom is SymptomClass.INTRINSIC:
        _audit(
            session, actor, "incident_not_grouped", incident.id,
            "opened by an intrinsic symptom", **ids,
        )  # fmt: skip
        return _open_group(session, config, incident, now)

    transition(
        session,
        IncidentRow,
        incident.id,
        expected_state=IncidentState.RECEIVED,
        expected_version=incident.version,
        target_state=IncidentState.GROUPED,
        audit=AuditSpec(
            actor=actor,
            event_type="incident_grouped",
            reason=f"related to {primary.service_id} within {config.window_seconds}s",
            correlation_ids=ids,
        ),
        extra_values={"group_id": group.id},
    )
    _extend_window(session, config, group, now)
    return GroupDecision(group.id, False)


# -- following the primary ------------------------------------------------------


def group_of_primary(session: Session, incident_id: str) -> IncidentGroupRow | None:
    return session.scalars(
        select(IncidentGroupRow).where(IncidentGroupRow.primary_incident_id == incident_id)
    ).first()


def is_demoted(session: Session, incident: IncidentRow) -> bool:
    """True for an unsplit group member that is no longer (or never was) the primary."""
    if incident.group_id is None or incident.group_split_at is not None:
        return False
    group = session.get(IncidentGroupRow, incident.group_id)
    return group is not None and group.primary_incident_id != incident.id


def grouped_members(session: Session, group_id: str, exclude: str) -> list[IncidentRow]:
    return list(
        session.scalars(
            select(IncidentRow)
            .where(
                IncidentRow.group_id == group_id,
                IncidentRow.id != exclude,
                IncidentRow.group_split_at.is_(None),
                IncidentRow.state == IncidentState.GROUPED.value,
            )
            .order_by(IncidentRow.id)
            .with_for_update()
        )
    )


def split_member(
    session: Session, member: IncidentRow, *, actor: str, reason: str, note: str
) -> int:
    """GROUPED -> RECEIVED with an investigation job; group_id stays for history."""
    from incidentops.persistence.transitions import AuditSpec, OutboxSpec, transition

    return transition(
        session,
        IncidentRow,
        member.id,
        expected_state=IncidentState.GROUPED,
        expected_version=member.version,
        target_state=IncidentState.RECEIVED,
        audit=AuditSpec(
            actor=actor,
            event_type="incident_group_split",
            reason=reason,
            correlation_ids={"group_id": member.group_id or ""},
        ),
        outbox=OutboxSpec(
            kind=INVESTIGATE_JOB,
            payload={
                "incident_id": member.id,
                "incident_version": member.version + 1,
                "note": note,
            },
        ),
        extra_values={"group_split_at": func.now(), "group_note": note[:500]},
    )


def follow_terminal_primary(
    session: Session,
    member: IncidentRow,
    primary_id: str,
    primary_state: IncidentState,
    actor: str,
) -> None:
    """Apply a terminal primary outcome to one GROUPED member."""
    from incidentops.persistence.transitions import AuditSpec, transition

    if primary_state is IncidentState.RESOLVED:
        # Only the verifier may resolve: it checks this member's alerts.
        session.add(
            OutboxJobRow(
                id=new_id("job"),
                kind=VERIFY_GROUP_MEMBERS_JOB,
                aggregate_id=member.id,
                dedup_key=f"{VERIFY_GROUP_MEMBERS_JOB}:{member.id}:{member.version}",
                payload={"incident_id": member.id, "primary_incident_id": primary_id},
            )
        )
        return
    if primary_state in _ESCALATING:
        transition(
            session,
            IncidentRow,
            member.id,
            expected_state=IncidentState.GROUPED,
            expected_version=member.version,
            target_state=IncidentState.ESCALATED,
            audit=AuditSpec(
                actor=actor,
                event_type="incident_escalated",
                reason=f"group primary {primary_id} ended {primary_state.value}",
                correlation_ids={
                    "group_id": member.group_id or "",
                    "primary_incident_id": primary_id,
                },
            ),
            extra_values={"escalation_reason": "group_primary_escalated"},
        )
        return
    if primary_state is IncidentState.CANCELLED:
        split_member(
            session,
            member,
            actor=actor,
            reason=f"group primary {primary_id} was cancelled",
            note="the group primary was cancelled",
        )


def on_incident_terminal(
    session: Session, incident_id: str, state: IncidentState, actor: str
) -> None:
    """Members follow their primary into its terminal outcome (same transaction)."""
    group = group_of_primary(session, incident_id)
    if group is None:
        return
    for member in grouped_members(session, group.id, exclude=incident_id):
        follow_terminal_primary(session, member, incident_id, state, actor)


# -- read models --------------------------------------------------------------------


def latest_alert_statuses(session: Session, incident_id: str) -> dict[str, AlertStatus]:
    """The latest delivery status per alert fingerprint of an incident."""
    latest: dict[str, AlertStatus] = {}
    for fingerprint, status in session.execute(
        select(AlertEventRow.fingerprint, AlertEventRow.status)
        .where(AlertEventRow.incident_id == incident_id)
        .order_by(AlertEventRow.received_at.desc(), AlertEventRow.id.desc())
    ):
        latest.setdefault(fingerprint, AlertStatus(status))
    return latest


def still_firing(session: Session, incident_id: str) -> bool:
    return any(
        s is AlertStatus.FIRING for s in latest_alert_statuses(session, incident_id).values()
    )


def group_context(session: Session, incident: IncidentRow) -> dict[str, Any] | None:
    """Group facts for the primary's investigation context (data, not instructions)."""
    if incident.group_id is None:
        return None
    context: dict[str, Any] = {"group_id": incident.group_id}
    if incident.group_split_at is not None:
        context["split_from_group"] = True
        context["split_note"] = incident.group_note
        return context
    members = session.scalars(
        select(IncidentRow)
        .where(
            IncidentRow.group_id == incident.group_id,
            IncidentRow.id != incident.id,
            IncidentRow.group_split_at.is_(None),
        )
        .order_by(IncidentRow.created_at)
    ).all()
    if not members:
        return None
    listed: list[dict[str, Any]] = []
    for member in members:
        alerts = session.scalars(
            select(AlertEventRow.payload)
            .where(AlertEventRow.incident_id == member.id)
            .order_by(AlertEventRow.received_at)
        ).all()
        names = sorted({str(p.get("alert_name")) for p in alerts if p.get("alert_name")})
        starts = sorted(
            str(p.get("starts_at"))
            for p in alerts
            if p.get("status") == "firing" and p.get("starts_at")
        )
        entry: dict[str, Any] = {
            "service_id": member.service_id,
            "state": member.state,
            "alert_names": names,
            "first_firing_at": starts[0] if starts else None,
        }
        diagnosis = session.scalars(
            select(DiagnosisRow)
            .join(InvestigationRunRow, InvestigationRunRow.id == DiagnosisRow.run_id)
            .where(InvestigationRunRow.incident_id == member.id)
            .order_by(DiagnosisRow.created_at.desc())
        ).first()
        if diagnosis is not None:
            report = diagnosis.report.get("diagnosis", {})
            entry["earlier_diagnosis"] = {
                "root_cause_id": report.get("root_cause_id"),
                "summary": str(report.get("summary", ""))[:200],
            }
        listed.append(entry)
    context["role"] = "primary"
    context["related_incidents"] = listed
    return context


# -- manual split (G5) --------------------------------------------------------------


class UngroupOutcome(StrEnum):
    UNGROUPED = "ungrouped"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    NOT_GROUPED = "not_grouped"
    VERSION_CONFLICT = "version_conflict"


@dataclass(frozen=True)
class UngroupResult:
    outcome: UngroupOutcome
    version: int | None = None


def ungroup(
    session: Session,
    incident_id: str,
    username: str,
    allowed: bool,
    expected_version: int,
    reason: str | None,
) -> UngroupResult:
    """A person splits a non-primary member out of its group (audited)."""
    if not allowed:
        return UngroupResult(UngroupOutcome.FORBIDDEN)
    incident = session.get(IncidentRow, incident_id, with_for_update=True)
    if incident is None:
        return UngroupResult(UngroupOutcome.NOT_FOUND)
    state = IncidentState(incident.state)
    if not is_demoted(session, incident) or INCIDENT_MACHINE.is_terminal(state):
        return UngroupResult(UngroupOutcome.NOT_GROUPED, incident.version)
    if incident.version != expected_version:
        return UngroupResult(UngroupOutcome.VERSION_CONFLICT, incident.version)
    actor = f"user:{username}"
    why = f"split by {username}" + (f": {reason}" if reason else "")
    note = "split out by a person" + (f": {reason}" if reason else "")
    if state is IncidentState.GROUPED:
        version = split_member(session, incident, actor=actor, reason=why, note=note)
        return UngroupResult(UngroupOutcome.UNGROUPED, version)
    # A replaced primary whose run is still going keeps its state; its result
    # is routed normally (with its proposal) once the run finishes.
    bumped = session.execute(
        update(IncidentRow)
        .where(IncidentRow.id == incident.id, IncidentRow.version == incident.version)
        .values(
            version=IncidentRow.version + 1,
            updated_at=func.now(),
            group_split_at=func.now(),
            group_note=note[:500],
        )
        .returning(IncidentRow.version)
    ).scalar_one_or_none()
    if bumped is None:  # pragma: no cover - the row is locked above
        return UngroupResult(UngroupOutcome.VERSION_CONFLICT, incident.version)
    _audit(
        session, actor, "incident_group_split", incident.id, why, group_id=incident.group_id or ""
    )
    return UngroupResult(UngroupOutcome.UNGROUPED, int(bumped))
