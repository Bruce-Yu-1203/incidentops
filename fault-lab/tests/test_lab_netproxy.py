"""Kafka network-partition proxy (B-F07): forwarding, blocking and control auth."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from fault_lab.netproxy.app import TcpProxy, create_app

TOKEN = "internal-token-0123456789"


async def _echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while data := await reader.read(1024):
        writer.write(data)
        await writer.drain()
    writer.close()


@pytest.fixture
async def proxied() -> AsyncIterator[tuple[TcpProxy, int]]:
    upstream = await asyncio.start_server(_echo, "127.0.0.1", 0)
    upstream_port = upstream.sockets[0].getsockname()[1]
    proxy = TcpProxy("127.0.0.1", upstream_port)
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    yield proxy, server.sockets[0].getsockname()[1]
    proxy.unblock()
    server.close()
    upstream.close()


async def _read(reader: asyncio.StreamReader) -> bytes:
    """Bytes read, or b"" when the proxy closed or reset the connection."""
    try:
        return await asyncio.wait_for(reader.read(1024), timeout=2)
    except ConnectionError:
        return b""


async def _roundtrip(port: int, payload: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(payload)
        await writer.drain()
    except ConnectionError:
        return b""
    data = await _read(reader)
    writer.close()
    with contextlib.suppress(ConnectionError):
        await writer.wait_closed()
    return data


async def test_forwards_bytes_in_both_directions(proxied: tuple[TcpProxy, int]) -> None:
    _, port = proxied
    assert await _roundtrip(port, b"metadata-request") == b"metadata-request"


async def test_block_closes_open_connections_and_refuses_new_ones(
    proxied: tuple[TcpProxy, int],
) -> None:
    proxy, port = proxied
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"x")
    await writer.drain()
    assert await reader.read(1) == b"x"
    await proxy.block(60)
    # The open connection is cut ...
    assert await _read(reader) == b""
    writer.close()
    # ... and a new one is accepted and closed immediately.
    assert await _roundtrip(port, b"y") == b""
    proxy.unblock()
    assert await _roundtrip(port, b"z") == b"z"


async def test_partition_restores_itself_after_the_bound(proxied: tuple[TcpProxy, int]) -> None:
    proxy, port = proxied
    await proxy.block(0.05)
    assert proxy.blocked
    await asyncio.sleep(0.15)
    assert not proxy.blocked
    assert await _roundtrip(port, b"ok") == b"ok"


def test_control_endpoint_needs_the_internal_token() -> None:
    proxy = TcpProxy("127.0.0.1", 9)
    client = TestClient(create_app(proxy, TOKEN, listen_port=None))
    body = {"blocked": True, "seconds": 30}
    assert client.post("/internal/partition", json=body).status_code == 403
    wrong = {"Authorization": "Bearer nope"}
    assert client.post("/internal/partition", json=body, headers=wrong).status_code == 403
    assert (
        TestClient(create_app(proxy, None, None))
        .post("/internal/partition", json=body, headers={"Authorization": f"Bearer {TOKEN}"})
        .status_code
        == 403
    )
    with client:
        auth = {"Authorization": f"Bearer {TOKEN}"}
        assert client.post("/internal/partition", json=body, headers=auth).json() == {
            "blocked": True
        }
        assert client.post("/internal/partition", json={"blocked": False}, headers=auth).json() == {
            "blocked": False
        }


@pytest.mark.parametrize(
    "body", [{"blocked": True, "seconds": 0}, {"blocked": True, "seconds": 9999}]
)
def test_partition_duration_is_bounded(body: dict[str, object]) -> None:
    client = TestClient(create_app(TcpProxy("127.0.0.1", 9), TOKEN, None))
    response = client.post(
        "/internal/partition", json=body, headers={"Authorization": f"Bearer {TOKEN}"}
    )
    assert response.status_code == 422


def test_proxy_exposes_no_metrics() -> None:
    # Whether the partition is on is ground truth; nothing may scrape it.
    client = TestClient(create_app(TcpProxy("127.0.0.1", 9), TOKEN, None))
    assert client.get("/metrics").status_code == 404
