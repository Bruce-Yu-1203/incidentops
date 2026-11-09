"""Compare-and-swap transitions, audit and outbox atomicity on real PostgreSQL."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from incidentops.domain.ids import new_id
from incidentops.domain.state_machines import ApprovalState, IllegalTransitionError, IncidentState
from incidentops.persistence.models import (
    ActionProposalRow,
    ApprovalRequestRow,
    AuditEventRow,
    IncidentRow,
    OutboxJobRow,
)
from incidentops.persistence.transitions import (
    AuditSpec,
    OutboxSpec,
    TransitionConflictError,
    transition,
)

pytestmark = pytest.mark.integration

HASH = "sha256:" + "b" * 64
AUDIT = AuditSpec(actor="test", event_type="state_changed")


def _incident(session: Session, dedup_key: str = "lab:kafka-lag") -> str:
    incident_id = new_id("inc")
    session.add(
        IncidentRow(
            id=incident_id,
            dedup_key=dedup_key,
            service_id="persist-demo-consumer",
            environment="fault-lab",
            severity="warning",
            state=IncidentState.RECEIVED.value,
            version=1,
        )
    )
    session.flush()
    return incident_id


def _approval(session: Session) -> str:
    incident_id = _incident(session)
    action_id = new_id("act")
    expires = datetime.now(UTC) + timedelta(minutes=15)
    session.add(
        ActionProposalRow(
            id=action_id,
            incident_id=incident_id,
            action_type="resume_test_consumer",
            environment="fault-lab",
            target_id="persist-demo-consumer",
            target_revision="gen-8",
            envelope={},
            action_hash=HASH,
            policy_version="p1",
            expires_at=expires,
            state="PENDING_APPROVAL",
            version=1,
        )
    )
    approval_id = new_id("apr")
    session.add(
        ApprovalRequestRow(
            id=approval_id,
            action_id=action_id,
            action_hash=HASH,
            requested_by="run_x",
            policy_version="p1",
            expires_at=expires,
            state=ApprovalState.PENDING.value,
            version=1,
        )
    )
    session.flush()
    return approval_id


def _count(session: Session, model: type[AuditEventRow | OutboxJobRow]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_transition_bumps_version_and_writes_audit_and_outbox(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        incident_id = _incident(session)
        new_version = transition(
            session,
            IncidentRow,
            incident_id,
            expected_state=IncidentState.RECEIVED,
            expected_version=1,
            target_state=IncidentState.TRIAGED,
            audit=AUDIT,
            outbox=OutboxSpec(kind="investigate", payload={"incident_id": incident_id}),
        )
    assert new_version == 2
    with session_factory() as session:
        row = session.get_one(IncidentRow, incident_id)
        assert (row.state, row.version) == ("TRIAGED", 2)
        audit = session.scalars(select(AuditEventRow)).one()
        assert audit.correlation_ids["from_state"] == "RECEIVED"
        assert audit.correlation_ids["to_state"] == "TRIAGED"
        job = session.scalars(select(OutboxJobRow)).one()
        assert job.dedup_key == f"investigate:{incident_id}:2"


def test_stale_version_is_a_conflict(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        incident_id = _incident(session)
    with session_factory() as session, pytest.raises(TransitionConflictError):
        transition(
            session,
            IncidentRow,
            incident_id,
            expected_state=IncidentState.RECEIVED,
            expected_version=7,
            target_state=IncidentState.TRIAGED,
            audit=AUDIT,
        )


def test_illegal_transition_rejected_before_database(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        incident_id = _incident(session)
    with session_factory() as session:
        with pytest.raises(IllegalTransitionError):
            transition(
                session,
                IncidentRow,
                incident_id,
                expected_state=IncidentState.RECEIVED,
                expected_version=1,
                target_state=IncidentState.RESOLVED,
                audit=AUDIT,
            )
        assert _count(session, AuditEventRow) == 0


def test_failed_transaction_leaves_no_partial_writes(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        incident_id = _incident(session)
    with pytest.raises(RuntimeError, match="boom"), session_factory.begin() as session:
        transition(
            session,
            IncidentRow,
            incident_id,
            expected_state=IncidentState.RECEIVED,
            expected_version=1,
            target_state=IncidentState.TRIAGED,
            audit=AUDIT,
            outbox=OutboxSpec(kind="investigate", payload={}),
        )
        raise RuntimeError("boom")
    with session_factory() as session:
        assert session.get_one(IncidentRow, incident_id).state == "RECEIVED"
        assert _count(session, AuditEventRow) == 0
        assert _count(session, OutboxJobRow) == 0


def test_concurrent_approvals_produce_one_decision_and_one_job(
    session_factory: sessionmaker[Session],
) -> None:
    # S-05 at the persistence layer: two approvers race on the same version.
    with session_factory.begin() as session:
        approval_id = _approval(session)

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def approve() -> None:
        session = session_factory()
        try:
            barrier.wait()
            transition(
                session,
                ApprovalRequestRow,
                approval_id,
                expected_state=ApprovalState.PENDING,
                expected_version=1,
                target_state=ApprovalState.APPROVED,
                audit=AuditSpec(actor="approver", event_type="approval_decided"),
                outbox=OutboxSpec(kind="execute_action", payload={"approval_id": approval_id}),
            )
            session.commit()
            result = "won"
        except TransitionConflictError:
            session.rollback()
            result = "conflict"
        finally:
            session.close()
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=approve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(outcomes) == ["conflict", "won"]
    with session_factory() as session:
        assert _count(session, OutboxJobRow) == 1
        assert _count(session, AuditEventRow) == 1
        assert session.get_one(ApprovalRequestRow, approval_id).version == 2


def test_only_one_active_incident_per_dedup_key(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as session:
        first = _incident(session, "dup-key")
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        _incident(session, "dup-key")

    # After the first incident reaches a terminal state, a new one may open.
    with session_factory.begin() as session:
        transition(
            session,
            IncidentRow,
            first,
            expected_state=IncidentState.RECEIVED,
            expected_version=1,
            target_state=IncidentState.CANCELLED,
            audit=AUDIT,
        )
    with session_factory.begin() as session:
        _incident(session, "dup-key")


def test_outbox_dedup_key_blocks_double_enqueue(session_factory: sessionmaker[Session]) -> None:
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        for _ in range(2):
            session.add(
                OutboxJobRow(
                    id=new_id("job"), kind="k", aggregate_id="a", dedup_key="same", payload={}
                )
            )
            session.flush()


def test_state_check_constraint_rejects_unknown_state(
    session_factory: sessionmaker[Session],
) -> None:
    with pytest.raises(IntegrityError), session_factory.begin() as session:
        incident_id = _incident(session)
        session.execute(
            text("UPDATE incidents SET state = 'HACKED' WHERE id = :id"), {"id": incident_id}
        )


@pytest.mark.parametrize(
    "statement",
    ["UPDATE audit_events SET actor = 'mallory'", "DELETE FROM audit_events"],
)
def test_audit_events_are_append_only(
    session_factory: sessionmaker[Session], statement: str
) -> None:
    with session_factory.begin() as session:
        incident_id = _incident(session)
        transition(
            session,
            IncidentRow,
            incident_id,
            expected_state=IncidentState.RECEIVED,
            expected_version=1,
            target_state=IncidentState.TRIAGED,
            audit=AUDIT,
        )
    with (
        pytest.raises((InternalError, ProgrammingError), match="append-only"),
        session_factory.begin() as session,
    ):
        session.execute(text(statement))
    with session_factory() as session:
        assert session.scalars(select(AuditEventRow)).one().actor == "test"
