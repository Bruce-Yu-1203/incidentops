"""Instrumented demo API backed by lab PostgreSQL and Redis.

``GET /items/{id}`` reads ``lab_items`` directly from PostgreSQL (no cache),
so a database slowdown shows up as API latency and errors. Every request gets
a server span and the database call a child span for trace correlation.
"""

from __future__ import annotations

import hmac
import logging
import os
import time
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal, Protocol

from fastapi import FastAPI, Header, HTTPException, Path, Request, Response
from fastapi.responses import PlainTextResponse
from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    ProcessCollector,
    generate_latest,
)
from pydantic import BaseModel, ConfigDict, Field

from fault_lab import telemetry
from fault_lab.api.behaviour import Behaviour, InjectedFailureError, Profile, unit_price_cents

LOGGER = logging.getLogger("fault_lab.api")
ITEM_COUNT = 1000


class Dependencies(Protocol):
    async def prepare(self) -> None: ...
    async def check_database(self) -> None: ...
    async def check_cache(self) -> None: ...
    async def touch_cache(self) -> None: ...
    async def count_events(self) -> int: ...
    async def get_item(self, item_id: int, database: str = "lab") -> dict[str, Any] | None: ...


class BehaviourUpdate(BaseModel):
    """Internal control message from the fault controller (lab-internal token)."""

    model_config = ConfigDict(extra="forbid")
    release_id: Annotated[str, Field(pattern=r"^rel-[0-9]{1,6}$")] | None = None
    profile: Profile | None = None
    cpu_load: bool | None = None
    noise_seconds: Annotated[float, Field(ge=0, le=600)] | None = None
    error_path: bool | None = None
    # Only known keys with identifier-like values (never DSNs or hosts).
    config: (
        dict[
            Literal["items_database"],
            Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")],
        ]
        | None
    ) = None
    reset: bool = False


def create_app(
    deps: Dependencies,
    tracer: trace.Tracer | None = None,
    behaviour: Behaviour | None = None,
    internal_token: str | None = None,
) -> FastAPI:
    tracer = tracer or trace.get_tracer("lab-api")
    behaviour = behaviour or Behaviour()
    registry = CollectorRegistry()
    # Process memory and CPU are ordinary service telemetry (agent-visible).
    ProcessCollector(registry=registry)
    release_info = Gauge(
        "lab_api_release_info", "Currently running release", ["release"], registry=registry
    )
    requests = Counter(
        "lab_api_http_requests",
        "HTTP requests by route and status",
        ["route", "status"],
        registry=registry,
    )
    latency = Histogram(
        "lab_api_http_request_duration_seconds",
        "HTTP request latency",
        ["route"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
        registry=registry,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await deps.prepare()
        yield

    app = FastAPI(title="Fault Lab demo API", lifespan=lifespan)

    @app.middleware("http")
    async def instrument(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Internal fault-control calls are never instrumented: their timing
        # would reveal benchmark injections in agent-visible telemetry.
        if request.url.path in ("/metrics", "/healthz") or request.url.path.startswith(
            "/internal/"
        ):
            return await call_next(request)
        start = time.perf_counter()
        status = 500
        with tracer.start_as_current_span(
            f"{request.method} {request.url.path}", kind=SpanKind.SERVER
        ) as span:
            try:
                response = await call_next(request)
                status = response.status_code
                trace_id = telemetry.current_trace_id()
                if trace_id:
                    response.headers["X-Trace-Id"] = trace_id
                return response
            finally:
                route = request.scope.get("route")
                path = getattr(route, "path", "unmatched")
                span.set_attribute("http.route", path)
                span.set_attribute("http.response.status_code", status)
                if status >= 500:
                    span.set_status(Status(StatusCode.ERROR))
                requests.labels(route=path, status=str(status)).inc()
                latency.labels(route=path).observe(time.perf_counter() - start)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        try:
            await deps.check_database()
            await deps.check_cache()
        except Exception as exc:
            LOGGER.warning("dependency check failed: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="dependency unavailable") from exc
        return {"status": "ready"}

    def _publish_release() -> None:
        release_info.clear()
        release_info.labels(release=behaviour.release_id).set(1)

    _publish_release()

    @app.post("/internal/behaviour")
    async def update_behaviour(
        body: BehaviourUpdate, authorization: Annotated[str | None, Header()] = None
    ) -> dict[str, object]:
        expected = f"Bearer {internal_token}" if internal_token else None
        if expected is None or not hmac.compare_digest(authorization or "", expected):
            raise HTTPException(status_code=403, detail="fault controller only")
        if body.reset:
            behaviour.reset()
        if body.release_id is not None and body.profile is not None:
            behaviour.deploy(body.release_id, body.profile)
        if body.cpu_load is True:
            behaviour.start_cpu_load()
        elif body.cpu_load is False:
            behaviour.stop_cpu_load()
        if body.noise_seconds is not None:
            behaviour.start_noise(body.noise_seconds)
        if body.error_path is not None:
            behaviour.error_path = body.error_path
        if body.config is not None:
            behaviour.config = {**behaviour.config, **{str(k): v for k, v in body.config.items()}}
        _publish_release()
        return behaviour.snapshot()

    @app.get("/items/{item_id}")
    async def get_item(item_id: Annotated[int, Path(ge=1, le=ITEM_COUNT)]) -> dict[str, Any]:
        try:
            behaviour.on_request()
        except InjectedFailureError as exc:
            LOGGER.error("request failed in release %s: %s", behaviour.release_id, exc)
            raise HTTPException(status_code=500, detail="internal error") from exc
        if behaviour.takes_error_path(item_id):
            try:
                unit_price_cents({"id": item_id})
            except Exception as exc:
                # The stack trace is part of the log line so a log search finds it.
                LOGGER.error(
                    "unhandled exception serving item %s\n%s", item_id, traceback.format_exc()
                )
                raise HTTPException(status_code=500, detail="internal error") from exc
        try:
            await deps.touch_cache()
        except Exception as exc:
            LOGGER.error("cache call failed: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="cache unavailable") from exc
        with tracer.start_as_current_span("SELECT lab_items", kind=SpanKind.CLIENT) as span:
            span.set_attribute("db.system", "postgresql")
            span.set_attribute("db.collection.name", "lab_items")
            try:
                item = await deps.get_item(item_id, behaviour.config["items_database"])
            except Exception as exc:
                span.set_status(Status(StatusCode.ERROR))
                LOGGER.error("item query failed: %s", type(exc).__name__)
                raise HTTPException(status_code=503, detail="database unavailable") from exc
        if item is None:
            raise HTTPException(status_code=404, detail="item not found")
        return item

    @app.get("/events/count")
    async def events_count() -> dict[str, Any]:
        try:
            return {"count": await deps.count_events()}
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse(generate_latest(registry).decode())

    return app


class LiveDependencies:  # pragma: no cover - exercised by the live lab smoke test
    """Real PostgreSQL/Redis access. Queries are bounded by statement_timeout."""

    def __init__(self, dsn: str, redis_url: str, statement_timeout_ms: int = 5000) -> None:
        import redis.asyncio as redis

        self._dsn = dsn
        self._redis_url = redis_url
        self._options = f"-c statement_timeout={statement_timeout_ms}"
        self._cache = redis.from_url(redis_url, socket_timeout=1, socket_connect_timeout=1)

    async def touch_cache(self) -> None:
        # Every item request counts a hit in Redis, so a Redis outage is visible
        # as API errors (B-F02).
        await self._cache.incr("lab:item_hits")

    async def _connect(self, database: str | None = None) -> Any:
        import psycopg

        # The configured database name only overrides dbname within the
        # server-side DSN; host, role and credentials never come from config.
        extra: dict[str, Any] = {"dbname": database} if database else {}
        return await psycopg.AsyncConnection.connect(
            self._dsn, connect_timeout=2, options=self._options, autocommit=True, **extra
        )

    async def prepare(self) -> None:
        conn = await self._connect()
        async with conn:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS lab_items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
            )
            await conn.execute(
                "INSERT INTO lab_items (id, name) SELECT g, 'item-' || g"
                " FROM generate_series(1, %s) AS g ON CONFLICT (id) DO NOTHING",
                (ITEM_COUNT,),
            )

    async def check_database(self) -> None:
        conn = await self._connect()
        async with conn:
            await conn.execute("SELECT 1")

    async def check_cache(self) -> None:
        import redis.asyncio as redis

        client = redis.from_url(self._redis_url, socket_timeout=2)
        try:
            await client.ping()
        finally:
            await client.aclose()

    async def count_events(self) -> int:
        conn = await self._connect()
        async with conn:
            cur = await conn.execute("SELECT to_regclass('public.demo_events') IS NOT NULL")
            exists = await cur.fetchone()
            if not (exists and exists[0]):
                return 0
            cur = await conn.execute("SELECT count(*) FROM demo_events")
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def get_item(self, item_id: int, database: str = "lab") -> dict[str, Any] | None:
        conn = await self._connect(database)
        async with conn:
            cur = await conn.execute("SELECT id, name FROM lab_items WHERE id = %s", (item_id,))
            row = await cur.fetchone()
            return {"id": row[0], "name": row[1]} if row else None


def main() -> None:  # pragma: no cover - container entry point
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    tracer = telemetry.configure("lab-api")
    app = create_app(
        LiveDependencies(os.environ["LAB_DATABASE_DSN"], os.environ["LAB_REDIS_URL"]),
        tracer,
        internal_token=os.environ.get("FAULT_LAB_INTERNAL_TOKEN"),
    )
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))  # noqa: S104


if __name__ == "__main__":  # pragma: no cover
    main()
