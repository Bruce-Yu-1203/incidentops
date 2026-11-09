"""Leased outbox job claims (at-least-once; never described as exactly-once).

A job is claimable when it is PENDING and available, or LEASED with an expired
lease (its worker died). Claims use ``FOR UPDATE SKIP LOCKED`` so concurrent
workers never receive the same job at the same time.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from incidentops.persistence.models import OutboxJobRow


def claim(session: Session, kind: str, owner: str, lease_seconds: int) -> OutboxJobRow | None:
    now = func.now()
    candidate = (
        select(OutboxJobRow.id)
        .where(
            OutboxJobRow.kind == kind,
            OutboxJobRow.available_at <= now,
            or_(
                OutboxJobRow.status == "PENDING",
                (OutboxJobRow.status == "LEASED") & (OutboxJobRow.lease_until < now),
            ),
        )
        .order_by(OutboxJobRow.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    statement = (
        update(OutboxJobRow)
        .where(OutboxJobRow.id == candidate)
        .values(
            status="LEASED",
            lease_owner=owner,
            lease_until=now + timedelta(seconds=lease_seconds),
            attempts=OutboxJobRow.attempts + 1,
        )
        .returning(OutboxJobRow)
    )
    return session.scalars(statement).one_or_none()


def complete(session: Session, job_id: str, owner: str) -> bool:
    """Mark done only if this worker still holds the lease."""
    result = session.execute(
        update(OutboxJobRow)
        .where(
            OutboxJobRow.id == job_id,
            OutboxJobRow.lease_owner == owner,
            OutboxJobRow.status == "LEASED",
        )
        .values(status="DONE", lease_until=None)
        .returning(OutboxJobRow.id)
    )
    return result.scalar_one_or_none() is not None


def release_for_retry(
    session: Session,
    job_id: str,
    owner: str,
    error: str,
    retry_delay_seconds: int,
    max_attempts: int,
) -> None:
    """Return a job to the queue, or mark it DEAD after ``max_attempts``."""
    job = session.get(OutboxJobRow, job_id, with_for_update=True)
    if job is None or job.lease_owner != owner:
        return
    job.last_error = error[:500]
    job.lease_owner = None
    job.lease_until = None
    if job.attempts >= max_attempts:
        job.status = "DEAD"
    else:
        job.status = "PENDING"
        job.available_at = func.now() + timedelta(seconds=retry_delay_seconds)
