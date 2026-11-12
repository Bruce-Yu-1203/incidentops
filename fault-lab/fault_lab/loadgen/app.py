"""HTTP workload against the demo API (seeded item selection).

The rate is the configured baseline unless the fault controller raises it
through the internal load-profile endpoint (B-F11 traffic surge); a reset
restores the baseline.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import PlainTextResponse
from prometheus_client import CollectorRegistry, Counter, generate_latest
from pydantic import BaseModel, ConfigDict, Field

MAX_RATE_PER_SECOND = 200.0


class LoadProfile(BaseModel):
    """Internal control message from the fault controller (lab-internal token)."""

    model_config = ConfigDict(extra="forbid")
    rate_per_second: Annotated[float, Field(gt=0, le=MAX_RATE_PER_SECOND)] | None = None
    reset: bool = False


LOGGER = logging.getLogger("fault_lab.loadgen")


def item_path(rng: random.Random, item_count: int = 1000) -> str:
    return f"/items/{rng.randint(1, item_count)}"


def create_app(
    target: str,
    rate_per_second: float,
    seed: int,
    run_loop: bool = True,
    internal_token: str | None = None,
) -> FastAPI:
    baseline = rate_per_second
    profile = {"rate": rate_per_second}
    registry = CollectorRegistry()
    outcomes = Counter(
        "lab_loadgen_requests", "Workload requests by outcome", ["outcome"], registry=registry
    )

    async def generate() -> None:  # pragma: no cover - exercised by the live lab
        rng = random.Random(seed)  # noqa: S311 - deterministic workload, not crypto
        limit = asyncio.Semaphore(100)

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
                await asyncio.sleep(1.0 / profile["rate"])

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

    @app.post("/internal/load-profile")
    async def load_profile(
        body: LoadProfile, authorization: Annotated[str | None, Header()] = None
    ) -> dict[str, float]:
        expected = f"Bearer {internal_token}" if internal_token else None
        if expected is None or not hmac.compare_digest(authorization or "", expected):
            raise HTTPException(status_code=403, detail="fault controller only")
        if body.reset:
            profile["rate"] = baseline
        elif body.rate_per_second is not None:
            profile["rate"] = body.rate_per_second
        LOGGER.info("load profile now %.1f requests/s", profile["rate"])
        return {"rate_per_second": profile["rate"]}

    return app


def main() -> None:  # pragma: no cover - container entry point
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    app = create_app(
        target=os.environ.get("LAB_LOAD_TARGET", "http://lab-api:8080"),
        rate_per_second=float(os.environ.get("LAB_LOAD_RPS", "10")),
        seed=int(os.environ.get("LAB_LOAD_SEED", "2042")),
        internal_token=os.environ.get("FAULT_LAB_INTERNAL_TOKEN"),
    )
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8083")))  # noqa: S104


if __name__ == "__main__":  # pragma: no cover
    main()
