"""Benchmark-only fault controller: known, bounded, releasable fixtures.

P1 provides one fixture, a PostgreSQL lock held on ``lab_items`` by a known
transaction. It is addressed only by its fixture token (the inject action ID),
never by backend PID, so it can later back the ``release_lab_db_blocker``
action without exposing arbitrary backend termination.

The controller deliberately has no metrics endpoint: its state is ground truth
and must not reach agent-visible telemetry.
"""

# No ``from __future__ import annotations``: FastAPI resolves the locally
# defined dependency alias at runtime.

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from fault_lab.auth import LabAuthenticator, LabRole

LOGGER = logging.getLogger("fault_lab.faults")
MAX_HOLD_SECONDS = 600
FixtureToken = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]

# Acquire the lock, then wait until ``release`` is set or the hold expires.
LockHolder = Callable[[asyncio.Event, float], Coroutine[Any, Any, None]]


class FixtureConflictError(RuntimeError):
    pass


class FixtureNotFoundError(LookupError):
    pass


@dataclass
class ActiveLock:
    fixture_token: str
    started_at: datetime
    hold_seconds: float
    release: asyncio.Event
    task: asyncio.Task[None]


class DbLockFixture:
    """At most one active lock fixture at a time."""

    def __init__(self, holder: LockHolder) -> None:
        self._holder = holder
        self._active: ActiveLock | None = None

    def status(self) -> dict[str, Any]:
        active = self._active
        if active is None or active.task.done():
            return {"db_lock": None}
        return {
            "db_lock": {
                "fixture_token": active.fixture_token,
                "started_at": active.started_at.isoformat(),
                "hold_seconds": active.hold_seconds,
            }
        }

    async def inject(self, fixture_token: str, hold_seconds: float) -> dict[str, Any]:
        if self._active is not None and not self._active.task.done():
            raise FixtureConflictError("a db lock fixture is already active")
        release = asyncio.Event()
        task = asyncio.create_task(self._holder(release, hold_seconds))
        self._active = ActiveLock(fixture_token, datetime.now(UTC), hold_seconds, release, task)
        # Give the holder a moment to acquire the lock or fail fast.
        await asyncio.sleep(0.2)
        if task.done() and task.exception() is not None:
            self._active = None
            raise RuntimeError("failed to acquire lab db lock") from task.exception()
        return self.status()

    async def release(self, fixture_token: str) -> None:
        active = self._active
        if active is None or active.task.done() or active.fixture_token != fixture_token:
            raise FixtureNotFoundError(fixture_token)
        await self._stop(active)

    async def reset(self) -> None:
        if self._active is not None:
            await self._stop(self._active)

    async def _stop(self, active: ActiveLock) -> None:
        active.release.set()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(active.task, timeout=10)
        self._active = None


def postgres_lock_holder(dsn: str) -> LockHolder:  # pragma: no cover - live lab only
    async def hold(release: asyncio.Event, hold_seconds: float) -> None:
        import psycopg

        conn = await psycopg.AsyncConnection.connect(dsn, connect_timeout=3)
        async with conn:
            async with conn.transaction():
                await conn.execute("LOCK TABLE lab_items IN ACCESS EXCLUSIVE MODE")
                LOGGER.info("db lock fixture acquired for %.0fs", hold_seconds)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(release.wait(), timeout=hold_seconds)
            LOGGER.info("db lock fixture released")

    return hold


class InjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: FixtureToken
    hold_seconds: Annotated[float, Field(gt=0, le=MAX_HOLD_SECONDS)]


class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixture_token: FixtureToken


def create_app(fixture: DbLockFixture, authenticator: LabAuthenticator) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await fixture.reset()

    app = FastAPI(title="Fault Lab fault controller (benchmark only)", lifespan=lifespan)

    def benchmark_only(authorization: Annotated[str | None, Header()] = None) -> LabRole:
        role = authenticator.authenticate(authorization)
        if role is None:
            raise HTTPException(status_code=401, detail="unauthenticated")
        if role is not LabRole.BENCHMARK:
            raise HTTPException(status_code=403, detail="benchmark controller only")
        return role

    Benchmark = Annotated[LabRole, Depends(benchmark_only)]  # noqa: N806 - type alias

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/faults")
    async def status(_: Benchmark) -> dict[str, Any]:
        return fixture.status()

    @app.post("/faults/db-lock", status_code=201)
    async def inject(body: InjectRequest, _: Benchmark) -> dict[str, Any]:
        try:
            return await fixture.inject(body.action_id, body.hold_seconds)
        except FixtureConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/faults/db-lock/release")
    async def release(body: ReleaseRequest, _: Benchmark) -> dict[str, Any]:
        try:
            await fixture.release(body.fixture_token)
        except FixtureNotFoundError as exc:
            raise HTTPException(status_code=404, detail="no such active fixture") from exc
        return fixture.status()

    @app.post("/faults/reset")
    async def reset(_: Benchmark) -> dict[str, Any]:
        await fixture.reset()
        return fixture.status()

    return app


def main() -> None:  # pragma: no cover - container entry point
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    fixture = DbLockFixture(postgres_lock_holder(os.environ["LAB_DATABASE_DSN"]))
    app = create_app(fixture, LabAuthenticator.from_env())
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8090")))  # noqa: S104


if __name__ == "__main__":  # pragma: no cover
    main()
