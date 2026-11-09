"""Read-only telemetry backends used by tool executors.

Network destinations and credentials come exclusively from server-side
configuration; executors pass only server-built queries and catalog values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class Series:
    labels: dict[str, str]
    points: list[tuple[float, float]]  # (unix seconds, value)


@dataclass(frozen=True)
class LogLine:
    timestamp_ns: int
    line: str
    labels: dict[str, str]


@dataclass(frozen=True)
class TraceSummary:
    trace_id: str
    root_service: str
    root_name: str
    start_unix_ns: int
    duration_ms: int


class TelemetryBackend(Protocol):
    async def prometheus_range(
        self, query: str, start: datetime, end: datetime, step_seconds: int
    ) -> list[Series]: ...

    async def prometheus_instant(self, query: str) -> list[Series]: ...

    async def loki_query(
        self, logql: str, start: datetime, end: datetime, limit: int
    ) -> list[LogLine]: ...

    async def tempo_search(
        self, traceql: str, start: datetime, end: datetime, limit: int
    ) -> list[TraceSummary]: ...

    async def consumer_status(self, management_url: str) -> dict[str, Any]: ...


class BackendError(RuntimeError):
    """A telemetry backend returned an error or an unexpected shape."""


def _series(payload: dict[str, Any]) -> list[Series]:
    if payload.get("status") != "success":
        raise BackendError("prometheus query failed")
    result: list[Series] = []
    for item in payload["data"]["result"]:
        raw_points = item.get("values") or ([item["value"]] if "value" in item else [])
        points = [(float(ts), float(value)) for ts, value in raw_points]
        result.append(Series(labels=dict(item["metric"]), points=points))
    return result


class HttpTelemetryBackend:
    """HTTP clients for Prometheus, Loki, Tempo and the lab consumer status API."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        prometheus_url: str,
        loki_url: str,
        tempo_url: str,
        lab_read_token: str | None,
    ) -> None:
        self._client = client
        self._prometheus = prometheus_url.rstrip("/")
        self._loki = loki_url.rstrip("/")
        self._tempo = tempo_url.rstrip("/")
        self._read_token = lab_read_token

    async def _get(self, url: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        response = await self._client.get(url, params=params, **kwargs)
        if response.status_code >= 400:
            raise BackendError(f"{response.status_code} from telemetry backend")
        body = response.json()
        if not isinstance(body, dict):
            raise BackendError("unexpected response shape")
        return body

    async def prometheus_range(
        self, query: str, start: datetime, end: datetime, step_seconds: int
    ) -> list[Series]:
        body = await self._get(
            f"{self._prometheus}/api/v1/query_range",
            {
                "query": query,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step_seconds,
            },
        )
        return _series(body)

    async def prometheus_instant(self, query: str) -> list[Series]:
        return _series(await self._get(f"{self._prometheus}/api/v1/query", {"query": query}))

    async def loki_query(
        self, logql: str, start: datetime, end: datetime, limit: int
    ) -> list[LogLine]:
        body = await self._get(
            f"{self._loki}/loki/api/v1/query_range",
            {
                "query": logql,
                "start": int(start.timestamp() * 1e9),
                "end": int(end.timestamp() * 1e9),
                "limit": limit,
                "direction": "backward",
            },
        )
        lines: list[LogLine] = []
        for stream in body.get("data", {}).get("result", []):
            labels = dict(stream.get("stream", {}))
            for ts, line, *_ in stream.get("values", []):
                lines.append(LogLine(int(ts), str(line), labels))
        lines.sort(key=lambda entry: entry.timestamp_ns, reverse=True)
        return lines[:limit]

    async def tempo_search(
        self, traceql: str, start: datetime, end: datetime, limit: int
    ) -> list[TraceSummary]:
        body = await self._get(
            f"{self._tempo}/api/search",
            {
                "q": traceql,
                "start": int(start.timestamp()),
                "end": int(end.timestamp()),
                "limit": limit,
            },
        )
        return [
            TraceSummary(
                trace_id=str(t["traceID"]),
                root_service=str(t.get("rootServiceName", "")),
                root_name=str(t.get("rootTraceName", "")),
                start_unix_ns=int(t.get("startTimeUnixNano", 0)),
                duration_ms=int(t.get("durationMs", 0)),
            )
            for t in body.get("traces", [])
        ]

    async def consumer_status(self, management_url: str) -> dict[str, Any]:
        if not self._read_token:
            raise BackendError("lab read token not configured")
        return await self._get(
            f"{management_url.rstrip('/')}/control/status",
            {},
            headers={"Authorization": f"Bearer {self._read_token}"},
        )
