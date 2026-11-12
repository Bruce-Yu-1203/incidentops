"""Controllable Kafka test consumer with a protected management API.

Endpoints (ADR-009):
- ``GET  /control/status``                 any lab role
- ``POST /control/pause``                  benchmark role only (fault injection)
- ``POST /control/resume``                 executor role only (remediation)
- ``POST /control/reset``                  benchmark role only
- ``POST /control/processing-delay``       benchmark role only (B-F08 fault injection)
- ``GET  /control/operations/{action_id}`` any lab role (reconciliation)
- ``GET  /metrics``, ``GET /healthz``      unauthenticated, read-only
"""

# No ``from __future__ import annotations`` here: FastAPI must resolve the
# locally defined ``CallerRole`` dependency alias at runtime.

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Annotated, Any, Protocol

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest
from pydantic import BaseModel, ConfigDict, Field

from fault_lab import telemetry
from fault_lab.auth import LabAuthenticator, LabRole
from fault_lab.consumer.controller import ConsumerController, ControlError, Operation

LOGGER = logging.getLogger("fault_lab.consumer")
ActionId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class PauseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: ActionId
    expected_generation: str


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: ActionId
    desired_paused: bool
    expected_generation: str


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: ActionId


MAX_DELAY_MS = 1000


class DelayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: ActionId
    delay_ms: Annotated[int, Field(ge=0, le=MAX_DELAY_MS)]


class Throttle:
    """Injected per-message processing delay (B-F08 slow consumer).

    It is fault state, so it is never reported by the status endpoint or by
    metrics: agents only see the processing rate drop while lag grows.
    """

    def __init__(self) -> None:
        self.delay_ms = 0

    async def wait(self, records: int) -> None:
        if self.delay_ms and records:
            await asyncio.sleep(self.delay_ms * records / 1000)


class Metrics:
    """Per-app Prometheus registry so tests can create isolated apps."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.paused = Gauge(
            "lab_consumer_paused", "1 when the test consumer is paused", registry=self.registry
        )
        self.generation = Gauge(
            "lab_consumer_generation", "Current controller generation", registry=self.registry
        )
        self.processed = Counter(
            "lab_consumer_messages_processed",
            "Messages consumed and persisted",
            registry=self.registry,
        )
        self.errors = Counter(
            "lab_consumer_processing_errors", "Processing errors", registry=self.registry
        )


class MessageSink(Protocol):
    async def persist(self, records: list[dict[str, Any]]) -> None: ...


ConsumeLoop = Callable[[ConsumerController, Metrics], Coroutine[Any, Any, None]]


def create_app(
    controller: ConsumerController,
    authenticator: LabAuthenticator,
    consume_loop: ConsumeLoop | None = None,
    throttle: Throttle | None = None,
) -> FastAPI:
    metrics = Metrics()
    throttle = throttle or Throttle()

    def sync_metrics() -> None:
        status = controller.status()
        metrics.paused.set(1 if status.paused else 0)
        metrics.generation.set(int(status.generation.removeprefix("gen-")))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        sync_metrics()
        task: asyncio.Task[None] | None = (
            asyncio.create_task(consume_loop(controller, metrics)) if consume_loop else None
        )
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Fault Lab test consumer", lifespan=lifespan)
    app.state.metrics = metrics

    def role(authorization: Annotated[str | None, Header()] = None) -> LabRole:
        resolved = authenticator.authenticate(authorization)
        if resolved is None:
            raise HTTPException(status_code=401, detail="unauthenticated")
        return resolved

    CallerRole = Annotated[LabRole, Depends(role)]  # noqa: N806 - type alias

    @app.exception_handler(ControlError)
    async def _control_error(_: Request, exc: ControlError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"error": exc.code, "detail": str(exc)}
        )

    def _respond(operation: Operation, caller: LabRole, **kwargs: Any) -> JSONResponse:
        record, replayed = controller.apply(operation, role=caller, **kwargs)
        sync_metrics()
        LOGGER.info("control %s by %s action_id=%s", operation, caller, record.action_id)
        body = {"operation": record.to_json(), "replayed": replayed, **_status_json(controller)}
        return JSONResponse(status_code=200 if replayed else 201, content=body)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics_endpoint() -> PlainTextResponse:
        sync_metrics()
        return PlainTextResponse(generate_latest(metrics.registry).decode())

    @app.get("/control/status")
    async def status(_: CallerRole) -> dict[str, Any]:
        return _status_json(controller)

    @app.get("/control/operations/{action_id}")
    async def operation(action_id: str, _: CallerRole) -> dict[str, Any]:
        return controller.get_operation(action_id).to_json()

    @app.post("/control/pause")
    async def pause(body: PauseRequest, caller: CallerRole) -> JSONResponse:
        return _respond(
            Operation.PAUSE,
            caller,
            action_id=body.action_id,
            expected_generation=body.expected_generation,
        )

    @app.post("/control/resume")
    async def resume(body: ResumeRequest, caller: CallerRole) -> JSONResponse:
        return _respond(
            Operation.RESUME,
            caller,
            action_id=body.action_id,
            expected_generation=body.expected_generation,
            desired_paused=body.desired_paused,
        )

    @app.post("/control/reset")
    async def reset(body: ResetRequest, caller: CallerRole) -> JSONResponse:
        response = _respond(
            Operation.RESET, caller, action_id=body.action_id, expected_generation=None
        )
        throttle.delay_ms = 0
        return response

    @app.post("/control/processing-delay")
    async def processing_delay(body: DelayRequest, caller: CallerRole) -> dict[str, str]:
        if caller is not LabRole.BENCHMARK:
            raise HTTPException(status_code=403, detail="benchmark controller only")
        throttle.delay_ms = body.delay_ms
        LOGGER.info("control processing-delay by %s action_id=%s", caller, body.action_id)
        return {"status": "applied"}

    return app


def _status_json(controller: ConsumerController) -> dict[str, Any]:
    status = controller.status()
    return {
        "consumer_id": status.consumer_id,
        "paused": status.paused,
        "generation": status.generation,
    }


async def kafka_consume_loop(
    controller: ConsumerController,
    metrics: Metrics,
    sink: MessageSink,
    throttle: Throttle | None = None,
) -> None:  # pragma: no cover - exercised by the live lab smoke test
    """Consume ``demo.events.v1`` and persist records unless paused.

    While paused the loop keeps polling with all partitions paused, so the
    group membership is retained and no rebalance happens.
    """
    from aiokafka import AIOKafkaConsumer

    consumer = AIOKafkaConsumer(
        os.environ.get("LAB_TOPIC", "demo.events.v1"),
        bootstrap_servers=os.environ.get("LAB_KAFKA_BOOTSTRAP", "lab-kafka-proxy:9092"),
        group_id=controller.consumer_id,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    while True:
        try:
            await consumer.start()
            break
        except Exception:  # broker may still be starting
            LOGGER.warning("kafka not ready; retrying")
            await asyncio.sleep(2)
    try:
        while True:
            assignment = consumer.assignment()
            if controller.status().paused:
                consumer.pause(*assignment)
            else:
                consumer.resume(*assignment)
            batches = await consumer.getmany(timeout_ms=500, max_records=200)
            records = [
                json.loads(message.value)
                for messages in batches.values()
                for message in messages
                if message.value is not None
            ]
            if not records:
                continue
            if throttle is not None:
                await throttle.wait(len(records))
            try:
                await sink.persist(records)
            except Exception:
                metrics.errors.inc()
                LOGGER.exception("persist failed; will retry from last committed offset")
                await asyncio.sleep(1)
                for partition in batches:
                    committed = await consumer.committed(partition)
                    consumer.seek(partition, committed or 0)
                continue
            await consumer.commit()
            metrics.processed.inc(len(records))
    finally:
        await consumer.stop()


class PostgresSink:  # pragma: no cover - exercised by the live lab smoke test
    """Persist consumed events into the lab PostgreSQL."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def persist(self, records: list[dict[str, Any]]) -> None:
        import psycopg

        conn = await psycopg.AsyncConnection.connect(self._dsn)
        async with conn, conn.cursor() as cur:
            await cur.execute(
                "CREATE TABLE IF NOT EXISTS demo_events ("
                " event_id TEXT PRIMARY KEY, payload JSONB NOT NULL,"
                " stored_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            await cur.executemany(
                "INSERT INTO demo_events (event_id, payload) VALUES (%s, %s)"
                " ON CONFLICT (event_id) DO NOTHING",
                [(r["event_id"], json.dumps(r)) for r in records],
            )


def main() -> None:  # pragma: no cover - container entry point
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    # The catalog's service_id is the OTLP service.name, so search_logs can
    # find the consumer's logs in Loki.
    telemetry.configure(os.environ.get("LAB_CONSUMER_ID", "persist-demo-consumer"))
    controller = ConsumerController(
        os.environ.get("LAB_CONSUMER_ID", "persist-demo-consumer"),
        os.environ.get("LAB_LEDGER_PATH", "/data/ledger.sqlite3"),
    )
    sink = PostgresSink(os.environ["LAB_DATABASE_DSN"])
    throttle = Throttle()

    async def loop(ctrl: ConsumerController, metrics: Metrics) -> None:
        await kafka_consume_loop(ctrl, metrics, sink, throttle)

    app = create_app(controller, LabAuthenticator.from_env(), consume_loop=loop, throttle=throttle)
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8081")))  # noqa: S104


if __name__ == "__main__":  # pragma: no cover
    main()
