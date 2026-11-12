"""Fixed-rate, seeded event producer for ``demo.events.v1``."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest

from fault_lab import telemetry

LOGGER = logging.getLogger("fault_lab.producer")


def make_event(rng: random.Random, sequence: int) -> dict[str, Any]:
    """Build one deterministic demo event for a given seed and sequence."""
    return {
        "event_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
        "sequence": sequence,
        "kind": rng.choice(["view", "click", "purchase"]),
        "amount_cents": rng.randint(0, 10_000),
    }


def create_app(rate_per_second: float, seed: int, run_loop: bool = True) -> FastAPI:
    registry = CollectorRegistry()
    sent = Counter("lab_producer_messages_sent", "Messages produced", registry=registry)
    rate = Gauge("lab_producer_target_rate", "Configured messages per second", registry=registry)
    rate.set(rate_per_second)

    async def produce() -> None:  # pragma: no cover - exercised by the live lab smoke test
        from aiokafka import AIOKafkaProducer

        producer = AIOKafkaProducer(
            bootstrap_servers=os.environ.get("LAB_KAFKA_BOOTSTRAP", "lab-kafka:9092")
        )
        while True:
            try:
                await producer.start()
                break
            except Exception:
                LOGGER.warning("kafka not ready; retrying")
                await asyncio.sleep(2)
        rng = random.Random(seed)  # noqa: S311 - deterministic workload, not crypto
        topic = os.environ.get("LAB_TOPIC", "demo.events.v1")
        interval = 1.0 / rate_per_second
        sequence = 0
        try:
            while True:
                event = make_event(rng, sequence)
                await producer.send_and_wait(topic, json.dumps(event).encode())
                sent.inc()
                sequence += 1
                await asyncio.sleep(interval)
        finally:
            await producer.stop()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(produce()) if run_loop else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()

    app = FastAPI(title="Fault Lab producer", lifespan=lifespan)

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
    telemetry.configure("demo-producer")
    app = create_app(
        rate_per_second=float(os.environ.get("LAB_PRODUCER_RATE", "20")),
        seed=int(os.environ.get("LAB_PRODUCER_SEED", "1042")),
    )
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8082")))  # noqa: S104


if __name__ == "__main__":  # pragma: no cover
    main()
