"""Fixed-rate HTTP workload against the demo API (seeded item selection)."""

from __future__ import annotations

import asyncio
import logging
import os
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from prometheus_client import CollectorRegistry, Counter, generate_latest

LOGGER = logging.getLogger("fault_lab.loadgen")


def item_path(rng: random.Random, item_count: int = 1000) -> str:
    return f"/items/{rng.randint(1, item_count)}"


def create_app(target: str, rate_per_second: float, seed: int, run_loop: bool = True) -> FastAPI:
    registry = CollectorRegistry()
    outcomes = Counter(
        "lab_loadgen_requests", "Workload requests by outcome", ["outcome"], registry=registry
    )

    async def generate() -> None:  # pragma: no cover - exercised by the live lab
        rng = random.Random(seed)  # noqa: S311 - deterministic workload, not crypto
        limit = asyncio.Semaphore(100)
        interval = 1.0 / rate_per_second

        async with httpx.AsyncClient(base_url=target, timeout=15) as client:

            async def one(path: str) -> None:
                async with limit:
                    try:
                        response = await client.get(path)
                        outcomes.labels(outcome=str(response.status_code)).inc()
                    except httpx.HTTPError:
                        outcomes.labels(outcome="transport_error").inc()

            tasks: set[asyncio.Task[None]] = set()
            while True:
                task = asyncio.create_task(one(item_path(rng)))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
                await asyncio.sleep(interval)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(generate()) if run_loop else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()

    app = FastAPI(title="Fault Lab load generator", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse(generate_latest(registry).decode())

    return app


def main() -> None:  # pragma: no cover - container entry point
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    app = create_app(
        target=os.environ.get("LAB_LOAD_TARGET", "http://lab-api:8080"),
        rate_per_second=float(os.environ.get("LAB_LOAD_RPS", "10")),
        seed=int(os.environ.get("LAB_LOAD_SEED", "2042")),
    )
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8083")))  # noqa: S104


if __name__ == "__main__":  # pragma: no cover
    main()
