"""Fault controller: known, bounded, releasable fixtures plus allowlisted remediations.

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
from typing import TYPE_CHECKING, Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from fault_lab.auth import LabAuthenticator, LabRole

if TYPE_CHECKING:
    from fault_lab.faults.controller import LabController

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
    """At most one active fixture of this kind at a time.

    The holder decides what is held (a table lock for B-F01, idle connections
    for B-F12); the fixture only tracks its token, bound and release.
    """

    def __init__(self, holder: LockHolder, name: str = "db_lock") -> None:
        self._holder = holder
        self._name = name
        self._active: ActiveLock | None = None

    def status(self) -> dict[str, Any]:
        active = self._active
        if active is None or active.task.done():
            return {self._name: None}
        return {
            self._name: {
                "fixture_token": active.fixture_token,
                "started_at": active.started_at.isoformat(),
                "hold_seconds": active.hold_seconds,
            }
        }

    async def inject(self, fixture_token: str, hold_seconds: float) -> dict[str, Any]:
        if self._active is not None and not self._active.task.done():
            raise FixtureConflictError(f"a {self._name} fixture is already active")
        release = asyncio.Event()
        task = asyncio.create_task(self._holder(release, hold_seconds))
        self._active = ActiveLock(fixture_token, datetime.now(UTC), hold_seconds, release, task)
        # Give the holder a moment to acquire the lock or fail fast.
        await asyncio.sleep(0.2)
        if task.done() and task.exception() is not None:
            self._active = None
            raise RuntimeError(f"failed to start the {self._name} fixture") from task.exception()
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


MAX_HOARDED_CONNECTIONS = 200


def postgres_connection_hoarder(dsn: str) -> LockHolder:  # pragma: no cover - live lab only
    """Open idle connections as the API's own role until PostgreSQL refuses more.

    Simulates leaked application connections (B-F12): every non-reserved slot
    ends up held by an idle session, so the API cannot connect, while the
    monitoring role (``pg_use_reserved_connections``) and superusers can.
    """

    async def hold(release: asyncio.Event, hold_seconds: float) -> None:
        import psycopg

        held: list[Any] = []
        try:
            while len(held) < MAX_HOARDED_CONNECTIONS and not release.is_set():
                try:
                    held.append(await psycopg.AsyncConnection.connect(dsn, connect_timeout=3))
                except psycopg.OperationalError:
                    # Slots exhausted; keep trying in case a slot frees up.
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(release.wait(), timeout=0.5)
            LOGGER.info("holding %d idle connections for %.0fs", len(held), hold_seconds)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(release.wait(), timeout=hold_seconds)
        finally:
            for conn in held:
                with contextlib.suppress(Exception):
                    await conn.close()
            LOGGER.info("released %d idle connections", len(held))

    return hold


class InjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: FixtureToken
    hold_seconds: Annotated[float, Field(gt=0, le=MAX_HOLD_SECONDS)]


class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixture_token: FixtureToken


class FaultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: FixtureToken
    seconds: Annotated[float, Field(gt=0, le=MAX_HOLD_SECONDS)] = 300


class SurgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: FixtureToken
    rate_per_second: Annotated[float, Field(gt=0, le=200)]


class ConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: FixtureToken
    profile: Literal["bad_items_database"]


class DeployRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: FixtureToken
    profile: Literal["faulty", "leaky"]


class RemediationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: FixtureToken
    action_type: str
    target_id: Annotated[str, Field(pattern=r"^[a-z0-9-]{1,63}$")]
    expected_revision: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]
    arguments: dict[str, str | int | bool | None] = Field(default_factory=dict)


def create_app(
    fixture: DbLockFixture,
    authenticator: LabAuthenticator,
    controller: "LabController | None" = None,
    hoard: DbLockFixture | None = None,
) -> FastAPI:
    from fault_lab.faults.controller import (
        LabController,
        RemediationError,
        RemediationType,
    )

    lab = controller or LabController(fixture, _NullLabApi(), _NullRedis(), _MemoryLedger())
    connections = hoard or lab.connection_hoard

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await fixture.reset()
        await connections.reset()

    app = FastAPI(title="Fault Lab fault controller", lifespan=lifespan)

    def caller(authorization: Annotated[str | None, Header()] = None) -> LabRole:
        role = authenticator.authenticate(authorization)
        if role is None:
            raise HTTPException(status_code=401, detail="unauthenticated")
        return role

    def benchmark_only(role: Annotated[LabRole, Depends(caller)]) -> LabRole:
        if role is not LabRole.BENCHMARK:
            raise HTTPException(status_code=403, detail="benchmark controller only")
        return role

    def executor_only(role: Annotated[LabRole, Depends(caller)]) -> LabRole:
        if role is not LabRole.EXECUTOR:
            raise HTTPException(status_code=403, detail="executor only")
        return role

    Benchmark = Annotated[LabRole, Depends(benchmark_only)]  # noqa: N806 - type alias
    Executor = Annotated[LabRole, Depends(executor_only)]  # noqa: N806 - type alias
    AnyRole = Annotated[LabRole, Depends(caller)]  # noqa: N806 - type alias

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # ---- benchmark controller: inject and reset (ground truth stays here) ----

    @app.get("/faults")
    async def status(_: Benchmark) -> dict[str, Any]:
        return {
            **fixture.status(),
            **connections.status(),
            "lab": lab.state.__dict__ | {"deployments": None, "config_history": None},
        }

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

    @app.post("/faults/redis-outage", status_code=201)
    async def redis_outage(body: FaultRequest, _: Benchmark) -> dict[str, str]:
        await lab.pause_redis(body.seconds)
        return {"status": "injected"}

    @app.post("/faults/deploy", status_code=201)
    async def deploy(body: DeployRequest, _: Benchmark) -> dict[str, Any]:
        return await lab.deploy_release(body.profile)

    @app.post("/faults/cpu-load", status_code=201)
    async def cpu_load(body: FaultRequest, _: Benchmark) -> dict[str, str]:
        await lab.start_cpu_load()
        return {"status": "injected"}

    @app.post("/faults/connection-hoard", status_code=201)
    async def connection_hoard(body: InjectRequest, _: Benchmark) -> dict[str, Any]:
        try:
            return await connections.inject(body.action_id, body.hold_seconds)
        except FixtureConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/faults/traffic-surge", status_code=201)
    async def traffic_surge(body: SurgeRequest, _: Benchmark) -> dict[str, str]:
        await lab.surge_traffic(body.rate_per_second)
        return {"status": "injected"}

    @app.post("/faults/config", status_code=201)
    async def bad_config(body: ConfigRequest, _: Benchmark) -> dict[str, Any]:
        await lab.apply_bad_config(body.profile)
        return {"status": "injected"}

    @app.post("/faults/kafka-outage", status_code=201)
    async def kafka_outage(body: FaultRequest, _: Benchmark) -> dict[str, str]:
        await lab.partition_kafka(body.seconds)
        return {"status": "injected"}

    @app.post("/faults/postgres-outage", status_code=201)
    async def postgres_outage(body: FaultRequest, _: Benchmark) -> dict[str, str]:
        await lab.refuse_postgres(body.seconds)
        return {"status": "injected"}

    @app.post("/faults/error-path", status_code=201)
    async def error_path(body: FaultRequest, _: Benchmark) -> dict[str, str]:
        await lab.enable_error_path()
        return {"status": "injected"}

    @app.post("/faults/noise", status_code=201)
    async def noise(body: FaultRequest, _: Benchmark) -> dict[str, str]:
        await lab.start_noise(body.seconds)
        return {"status": "injected"}

    @app.post("/faults/reset")
    async def reset(_: Benchmark) -> dict[str, Any]:
        await lab.reset()
        return fixture.status()

    # ---- read-only operational views (any lab role) ----

    @app.get("/targets/{target_id}")
    async def target(target_id: str, _: AnyRole) -> dict[str, Any]:
        revision = lab.target_revision(target_id)
        if revision is None:
            raise HTTPException(status_code=404, detail="unknown target")
        return {"target_id": target_id, "revision": revision}

    @app.get("/deployments/lab-api")
    async def deployments(_: AnyRole) -> dict[str, Any]:
        return lab.deployments()

    @app.get("/config-changes/lab-api")
    async def config_changes(_: AnyRole) -> dict[str, Any]:
        return lab.config_changes()

    @app.get("/remediations/operations/{action_id}")
    async def remediation_operation(action_id: str, role: AnyRole) -> dict[str, Any]:
        if role is LabRole.READER:
            raise HTTPException(status_code=403, detail="executor or benchmark only")
        record = lab.operation(action_id)
        if record is None:
            raise HTTPException(status_code=404, detail="operation not found")
        return record

    # ---- executor: the allowlisted remediations only ----

    @app.post("/remediations")
    async def remediate(body: RemediationRequest, _: Executor) -> JSONResponse:
        try:
            action_type = RemediationType(body.action_type)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="remediation not allowlisted") from exc
        try:
            receipt, replayed = await lab.remediate(
                body.action_id,
                action_type,
                body.target_id,
                body.expected_revision,
                dict(body.arguments),
            )
        except RemediationError as exc:
            return JSONResponse(
                status_code=exc.status_code, content={"error": exc.code, "detail": str(exc)}
            )
        return JSONResponse(status_code=200 if replayed else 201, content=receipt)

    return app


class _NullLabApi:
    async def update(self, **fields: Any) -> dict[str, Any]:
        return dict(fields)


class _NullRedis:
    async def pause(self, milliseconds: int) -> None:
        return None

    async def unpause(self) -> None:
        return None


class _MemoryLedger:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}

    def get(self, action_id: str) -> dict[str, Any] | None:
        return self._records.get(action_id)

    def put(self, action_id: str, record: dict[str, Any]) -> None:
        self._records[action_id] = record


class HttpLabApi:  # pragma: no cover - live lab only
    """Calls the lab API's internal behaviour endpoint with the lab-internal token."""

    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}

    async def update(self, **fields: Any) -> dict[str, Any]:
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(
                f"{self._base}/internal/behaviour", json=fields, headers=self._headers
            )
            response.raise_for_status()
            return dict(response.json())


class LiveRedis:  # pragma: no cover - live lab only
    """Makes the lab Redis unavailable without container control.

    ``pause`` sets a random ``requirepass`` and disconnects every other client,
    so the API and the exporter get authentication failures (Redis looks down
    to them). ``unpause`` uses the controller's own authenticated connection to
    clear the password. An automatic restore after the requested duration
    guarantees the outage is bounded. CLIENT PAUSE was not usable: a paused
    server also blocks CLIENT UNPAUSE.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._password: str | None = None
        self._restore: asyncio.Task[None] | None = None

    async def pause(self, milliseconds: int) -> None:
        import secrets

        import redis.asyncio as redis

        password = secrets.token_hex(16)
        client = redis.from_url(self._url, socket_timeout=2)
        try:
            await client.config_set("requirepass", password)
            await client.execute_command(  # type: ignore[no-untyped-call]
                "CLIENT", "KILL", "TYPE", "normal", "SKIPME", "yes"
            )
        finally:
            await client.aclose()
        self._password = password
        if self._restore is not None:
            self._restore.cancel()
        self._restore = asyncio.create_task(self._auto_restore(milliseconds / 1000))

    async def _auto_restore(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        await self.unpause()

    async def unpause(self) -> None:
        import redis.asyncio as redis

        if self._password is None:
            return
        client = redis.from_url(self._url, password=self._password, socket_timeout=2)
        try:
            await client.config_set("requirepass", "")
        finally:
            await client.aclose()
        self._password = None
        if self._restore is not None and self._restore is not asyncio.current_task():
            self._restore.cancel()
        self._restore = None


class HttpKafkaPartition:  # pragma: no cover - live lab only
    """Blocks the lab Kafka proxy, so every client loses the broker (B-F07)."""

    def __init__(self, proxy_url: str, token: str) -> None:
        self._base = proxy_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _post(self, body: dict[str, Any]) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(
                f"{self._base}/internal/partition", json=body, headers=self._headers
            )
            response.raise_for_status()

    async def start(self, seconds: float) -> None:
        await self._post({"blocked": True, "seconds": seconds})

    async def stop(self) -> None:
        await self._post({"blocked": False})


class HttpLoadProfile:  # pragma: no cover - live lab only
    """Changes the lab load generator's request rate (B-F11)."""

    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}

    async def _post(self, body: dict[str, Any]) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(
                f"{self._base}/internal/load-profile", json=body, headers=self._headers
            )
            response.raise_for_status()

    async def set_rate(self, rate_per_second: float) -> None:
        await self._post({"rate_per_second": rate_per_second})

    async def reset(self) -> None:
        await self._post({"reset": True})


class LivePostgresRefusal:  # pragma: no cover - live lab only
    """Makes the lab database refuse every connection, without container control.

    ``start`` sets ``ALLOW_CONNECTIONS false`` on the ``lab`` database and ends
    its existing sessions, so the API, the consumer and the exporter cannot
    reach PostgreSQL (``pg_up`` 0, B-F09). The controller works from the
    ``postgres`` maintenance database, so ``stop`` can always restore access;
    an automatic restore bounds the outage.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._restore: asyncio.Task[None] | None = None

    async def _run(self, *statements: str) -> None:
        import psycopg

        conn = await psycopg.AsyncConnection.connect(
            self._dsn, dbname="postgres", autocommit=True, connect_timeout=3
        )
        async with conn:
            for statement in statements:
                await conn.execute(statement)

    async def start(self, seconds: float) -> None:
        await self._run(
            "ALTER DATABASE lab ALLOW_CONNECTIONS false",
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
            " WHERE datname = 'lab' AND pid <> pg_backend_pid()",
        )
        if self._restore is not None:
            self._restore.cancel()
        self._restore = asyncio.create_task(self._auto_restore(seconds))

    async def _auto_restore(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        await self.stop()

    async def stop(self) -> None:
        await self._run("ALTER DATABASE lab ALLOW_CONNECTIONS true")
        if self._restore is not None and self._restore is not asyncio.current_task():
            self._restore.cancel()
        self._restore = None


def main() -> None:  # pragma: no cover - container entry point
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    from fault_lab.faults.controller import LabController, Ledger

    fixture = DbLockFixture(postgres_lock_holder(os.environ["LAB_DATABASE_DSN"]))
    hoard = DbLockFixture(
        postgres_connection_hoarder(os.environ["LAB_APP_DATABASE_DSN"]), name="connection_hoard"
    )
    controller = LabController(
        fixture,
        HttpLabApi(os.environ["LAB_API_URL"], os.environ["FAULT_LAB_INTERNAL_TOKEN"]),
        LiveRedis(os.environ["LAB_REDIS_URL"]),
        Ledger(os.environ.get("LAB_LEDGER_PATH", "/data/remediations.sqlite3")),
        kafka=HttpKafkaPartition(
            os.environ.get("LAB_KAFKA_PROXY_URL", "http://lab-kafka-proxy:8094"),
            os.environ["FAULT_LAB_INTERNAL_TOKEN"],
        ),
        postgres=LivePostgresRefusal(os.environ["LAB_DATABASE_DSN"]),
        load=HttpLoadProfile(
            os.environ.get("LAB_LOADGEN_URL", "http://lab-loadgen:8083"),
            os.environ["FAULT_LAB_INTERNAL_TOKEN"],
        ),
        connection_hoard=hoard,
    )
    app = create_app(fixture, LabAuthenticator.from_env(), controller, hoard)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8090")))  # noqa: S104


if __name__ == "__main__":  # pragma: no cover
    main()
