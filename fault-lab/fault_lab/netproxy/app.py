"""TCP proxy in front of the lab Kafka broker (B-F07 network partition).

Every Kafka client in the lab (producer, consumer, lag exporter) reaches the
broker through this proxy, because the broker advertises the proxy address on
its client listener. The fault controller can *block* the proxy for a bounded
time: open connections are closed and new ones are refused, so to every client
the broker is unavailable, while the broker process itself keeps running (no
container control, ADR-020/ADR-024).

The proxy is fault infrastructure. Its control endpoint accepts only the
lab-internal token, and it exposes no metrics: whether it is blocked is ground
truth and must not reach agent-visible telemetry.
"""

# No ``from __future__ import annotations``: FastAPI resolves the locally
# defined dependency alias at runtime.

import asyncio
import contextlib
import hmac
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

LOGGER = logging.getLogger("fault_lab.netproxy")
MAX_BLOCK_SECONDS = 600
CHUNK_BYTES = 65536


class TcpProxy:
    """Bidirectional byte forwarding with an on/off partition switch."""

    def __init__(self, upstream_host: str, upstream_port: int) -> None:
        self._host = upstream_host
        self._port = upstream_port
        self._writers: set[asyncio.StreamWriter] = set()
        self._restore: asyncio.Task[None] | None = None
        self.blocked = False

    @property
    def open_connections(self) -> int:
        return len(self._writers) // 2

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.blocked:
            await _close(writer)
            return
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port), timeout=5
            )
        except (OSError, TimeoutError):
            await _close(writer)
            return
        self._writers.update({writer, up_writer})
        try:
            await asyncio.gather(_pump(reader, up_writer), _pump(up_reader, writer))
        finally:
            self._writers.difference_update({writer, up_writer})
            await _close(writer)
            await _close(up_writer)

    async def block(self, seconds: float) -> None:
        """Partition for at most ``seconds``; an automatic restore bounds the fault."""
        self.blocked = True
        for writer in list(self._writers):
            await _close(writer)
        if self._restore is not None:
            self._restore.cancel()
        self._restore = asyncio.create_task(self._auto_restore(seconds))
        LOGGER.info("partition on for at most %.0fs", seconds)

    async def _auto_restore(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
        self.unblock()

    def unblock(self) -> None:
        self.blocked = False
        if self._restore is not None and self._restore is not asyncio.current_task():
            self._restore.cancel()
        self._restore = None
        LOGGER.info("partition off")


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    with contextlib.suppress(OSError, asyncio.IncompleteReadError):
        while True:
            data = await reader.read(CHUNK_BYTES)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    # Half-close is not needed by Kafka; end both directions together.
    await _close(writer)


async def _close(writer: asyncio.StreamWriter) -> None:
    with contextlib.suppress(Exception):
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), timeout=1)


class PartitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blocked: bool
    seconds: Annotated[float, Field(gt=0, le=MAX_BLOCK_SECONDS)] = 300


def create_app(proxy: TcpProxy, internal_token: str | None, listen_port: int | None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        server = (
            await asyncio.start_server(proxy.handle, "0.0.0.0", listen_port)  # noqa: S104
            if listen_port is not None
            else None
        )
        try:
            yield
        finally:
            proxy.unblock()
            if server is not None:
                server.close()

    app = FastAPI(title="Fault Lab Kafka proxy", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/internal/partition")
    async def partition(
        body: PartitionRequest, authorization: Annotated[str | None, Header()] = None
    ) -> dict[str, bool]:
        expected = f"Bearer {internal_token}" if internal_token else None
        if expected is None or not hmac.compare_digest(authorization or "", expected):
            raise HTTPException(status_code=403, detail="fault controller only")
        if body.blocked:
            await proxy.block(body.seconds)
        else:
            proxy.unblock()
        return {"blocked": proxy.blocked}

    return app


def main() -> None:  # pragma: no cover - container entry point
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    host, _, port = os.environ.get("LAB_PROXY_UPSTREAM", "lab-kafka:9092").partition(":")
    proxy = TcpProxy(host, int(port))
    app = create_app(
        proxy,
        os.environ.get("FAULT_LAB_INTERNAL_TOKEN"),
        int(os.environ.get("LAB_PROXY_LISTEN_PORT", "9092")),
    )
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8094")))  # noqa: S104


if __name__ == "__main__":  # pragma: no cover
    main()
