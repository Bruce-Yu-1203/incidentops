"""Lease heartbeats for long-running jobs."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from incidentops.domain.ids import new_id
from incidentops.persistence import outbox
from incidentops.persistence.models import OutboxJobRow
from incidentops.workers.common import process_next

pytestmark = pytest.mark.integration


def _job(factory: sessionmaker[Session], kind: str = "slow_job") -> str:
    job_id = new_id("job")
    with factory.begin() as session:
        session.add(
            OutboxJobRow(id=job_id, kind=kind, aggregate_id="x", dedup_key=job_id, payload={})
        )
    return job_id


def test_heartbeat_extends_only_the_owners_lease(session_factory: sessionmaker[Session]) -> None:
    job_id = _job(session_factory)
    with session_factory.begin() as session:
        assert outbox.claim(session, "slow_job", "a", 1) is not None
    with session_factory.begin() as session:
        assert outbox.heartbeat(session, job_id, "a", 600)
        assert not outbox.heartbeat(session, job_id, "b", 600)
    with session_factory() as session:
        remaining = session.scalar(
            text("SELECT extract(epoch FROM lease_until - now()) FROM outbox_jobs")
        )
    assert remaining is not None
    assert remaining > 500


async def test_running_job_is_not_stolen_while_heartbeating(
    session_factory: sessionmaker[Session],
) -> None:
    job_id = _job(session_factory)
    stolen: list[Any] = []

    async def slow(payload: dict[str, Any], attempts: int) -> None:
        # Longer than the 1 s lease: only heartbeats keep the job ours.
        for _round in range(4):
            await asyncio.sleep(0.6)
            with session_factory.begin() as session:
                stolen.append(outbox.claim(session, "slow_job", "thief", 1))

    assert await process_next(session_factory, {"slow_job": slow}, "worker", 1)
    assert stolen == [None, None, None, None]
    with session_factory() as session:
        job = session.scalars(select(OutboxJobRow)).one()
    assert (job.id, job.status, job.lease_owner) == (job_id, "DONE", "worker")


def test_crashed_worker_loses_lease(session_factory: sessionmaker[Session]) -> None:
    _job(session_factory)
    with session_factory.begin() as session:
        assert outbox.claim(session, "slow_job", "dead", 1) is not None
    with session_factory.begin() as session:
        session.execute(text("UPDATE outbox_jobs SET lease_until = now() - interval '1 second'"))
    with session_factory.begin() as session:
        job = outbox.claim(session, "slow_job", "alive", 60)
    assert job is not None
    assert (job.lease_owner, job.attempts) == ("alive", 2)
