"""Shared leased-job loop for worker processes."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from incidentops.persistence import outbox

LOGGER = logging.getLogger("incidentops.workers")
Handler = Callable[[dict[str, Any], int], Awaitable[None]]


def owner_id(name: str) -> str:
    return f"{name}-{socket.gethostname()}-{os.getpid()}"


async def process_next(
    factory: sessionmaker[Session],
    handlers: Mapping[str, Handler],
    owner: str,
    lease_seconds: int,
    max_attempts: int = 5,
    retry_delay_seconds: int = 15,
) -> bool:
    """Claim and handle one job of any registered kind; False if none was ready."""
    for kind, handler in handlers.items():
        with factory.begin() as session:
            job = outbox.claim(session, kind, owner, lease_seconds)
            if job is None:
                continue
            job_id, payload, attempts = job.id, dict(job.payload), job.attempts
        keeper = asyncio.create_task(
            _keep_lease(factory, job_id, owner, lease_seconds), name=f"lease-{job_id}"
        )
        try:
            await handler(payload, attempts)
        except Exception as exc:
            LOGGER.exception("%s job %s failed (attempt %s)", kind, job_id, attempts)
            with factory.begin() as session:
                outbox.release_for_retry(
                    session, job_id, owner, type(exc).__name__, retry_delay_seconds, max_attempts
                )
            return True
        finally:
            keeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await keeper
        with factory.begin() as session:
            outbox.complete(session, job_id, owner)
        return True
    return False


async def _keep_lease(
    factory: sessionmaker[Session], job_id: str, owner: str, lease_seconds: int
) -> None:
    """Renew the lease at a third of its length while the handler runs.

    A long verification window then cannot be stolen by another worker, while
    a crashed worker (no heartbeats) still loses the job after one lease.
    """
    interval = max(lease_seconds / 3, 0.05)
    while True:
        await asyncio.sleep(interval)
        with factory.begin() as session:
            if not outbox.heartbeat(session, job_id, owner, lease_seconds):
                LOGGER.warning("lease on job %s was lost", job_id)
                return


async def run_forever(
    factory: sessionmaker[Session],
    handlers: Mapping[str, Handler],
    owner: str,
    lease_seconds: int,
    idle: Callable[[], Awaitable[None]] | None = None,
    max_attempts: int = 5,
) -> None:  # pragma: no cover - process loop
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    LOGGER.info("worker %s handling %s", owner, sorted(handlers))
    while not stop.is_set():
        handled = await process_next(factory, handlers, owner, lease_seconds, max_attempts)
        if not handled:
            if idle is not None:
                await idle()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=2)
