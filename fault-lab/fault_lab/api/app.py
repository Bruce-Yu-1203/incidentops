"""Instrumented demo API backed by lab PostgreSQL and Redis."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

LOGGER = logging.getLogger("fault_lab.api")


class Dependencies(Protocol):
    async def check_database(self) -> None: ...
    async def check_cache(self) -> None: ...
    async def count_events(self) -> int: ...


def create_app(deps: Dependencies) -> FastAPI:
    registry = CollectorRegistry()
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
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
        registry=registry,
    )
    app = FastAPI(title="Fault Lab demo API")

    @app.middleware("http")
    async def instrument(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
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
    def __init__(self, dsn: str, redis_url: str) -> None:
        self._dsn = dsn
        self._redis_url = redis_url

    async def check_database(self) -> None:
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._dsn, connect_timeout=2) as conn:
            await conn.execute("SELECT 1")

    async def check_cache(self) -> None:
        import redis.asyncio as redis

        client = redis.from_url(self._redis_url, socket_timeout=2)
        try:
            await client.ping()
        finally:
            await client.aclose()

    async def count_events(self) -> int:
        import psycopg

        async with await psycopg.AsyncConnection.connect(self._dsn, connect_timeout=2) as conn:
            cur = await conn.execute(
                "SELECT count(*) FROM demo_events" if await _table_exists(conn) else "SELECT 0"
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0


async def _table_exists(conn: Any) -> bool:  # pragma: no cover
    cur = await conn.execute("SELECT to_regclass('public.demo_events') IS NOT NULL")
    row = await cur.fetchone()
    return bool(row and row[0])


def main() -> None:  # pragma: no cover - container entry point
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    app = create_app(LiveDependencies(os.environ["LAB_DATABASE_DSN"], os.environ["LAB_REDIS_URL"]))
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))  # noqa: S104


if __name__ == "__main__":  # pragma: no cover
    main()
