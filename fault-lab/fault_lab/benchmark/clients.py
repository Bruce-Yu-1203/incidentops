"""HTTP clients used by the benchmark controller."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from fault_lab.scripts.labctl import LabController, LabEndpoints


class LabFaults:
    """Benchmark-token operations on the lab (inject, reset, probe state)."""

    def __init__(self, client: httpx.Client, token: str, endpoints: LabEndpoints) -> None:
        self._client = client
        self._headers = {"Authorization": f"Bearer {token}"}
        self.endpoints = endpoints
        self.consumer = LabController(endpoints, token, client)

    def _post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._client.post(
            f"{self.endpoints.faults}{path}", json=body or {}, headers=self._headers
        )
        response.raise_for_status()
        return dict(response.json())

    def inject(self, operation: str, params: dict[str, Any], action_id: str) -> None:
        if operation == "pause_consumer":
            self.consumer.pause()
        elif operation == "hold_db_lock":
            self._post(
                "/faults/db-lock",
                {"action_id": action_id, "hold_seconds": params.get("hold_seconds", 300)},
            )
        elif operation == "redis_outage":
            self._post(
                "/faults/redis-outage",
                {"action_id": action_id, "seconds": params.get("seconds", 300)},
            )
        elif operation == "deploy_release":
            self._post("/faults/deploy", {"action_id": action_id, "profile": params["profile"]})
        elif operation == "start_cpu_load":
            self._post("/faults/cpu-load", {"action_id": action_id})
        elif operation == "kafka_outage":
            self._post(
                "/faults/kafka-outage",
                {"action_id": action_id, "seconds": params.get("seconds", 300)},
            )
        elif operation == "postgres_outage":
            self._post(
                "/faults/postgres-outage",
                {"action_id": action_id, "seconds": params.get("seconds", 300)},
            )
        elif operation == "enable_error_path":
            self._post("/faults/error-path", {"action_id": action_id})
        elif operation == "traffic_surge":
            self._post(
                "/faults/traffic-surge",
                {"action_id": action_id, "rate_per_second": params.get("rate_per_second", 60)},
            )
        elif operation == "hoard_connections":
            self._post(
                "/faults/connection-hoard",
                {"action_id": action_id, "hold_seconds": params.get("hold_seconds", 300)},
            )
        elif operation == "bad_config":
            self._post("/faults/config", {"action_id": action_id, "profile": params["profile"]})
        elif operation == "slow_consumer":
            self.consumer.slow(int(params.get("delay_ms", 100)), action_id)
        elif operation == "error_burst":
            self._post(
                "/faults/noise", {"action_id": action_id, "seconds": params.get("seconds", 60)}
            )
        else:
            raise ValueError(f"unknown injection {operation}")

    def reset(self) -> None:
        self.consumer.reset()  # also resets the fault controller

    def release(self) -> str | None:
        response = self._client.get(
            f"{self.endpoints.faults}/targets/lab-api", headers=self._headers
        )
        return str(response.json()["revision"]) if response.status_code == 200 else None

    def consumer_running(self) -> bool:
        return self.consumer.status()["paused"] is False


class Prometheus:
    def __init__(self, client: httpx.Client, base_url: str) -> None:
        self._client = client
        self._base = base_url.rstrip("/")

    def range_values(self, query: str, window_seconds: int, step: int = 5) -> list[float]:
        end = time.time()
        response = self._client.get(
            f"{self._base}/api/v1/query_range",
            params={"query": query, "start": end - window_seconds, "end": end, "step": step},
        )
        response.raise_for_status()
        result = response.json()["data"]["result"]
        return [float(v) for _, v in result[0]["values"]] if result else []

    def firing_lab_alerts(self, alertmanager_url: str) -> list[str]:
        response = self._client.get(f"{alertmanager_url.rstrip('/')}/api/v2/alerts")
        response.raise_for_status()
        return [
            a["labels"]["alertname"]
            for a in response.json()
            if a["labels"].get("environment") == "fault-lab"
        ]


@dataclass
class IncidentOpsApi:
    """The public IncidentOps API, used as the seeded benchmark approver."""

    client: httpx.Client
    base_url: str

    def login(self, username: str, password: str) -> None:
        response = self.client.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"username": username, "password": password},
        )
        response.raise_for_status()
        self.client.cookies.clear()
        self.client.headers["Authorization"] = f"Session {response.headers['X-Session-Token']}"

    def get(self, path: str, **params: Any) -> Any:
        response = self.client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        return response.json()

    def incidents(self) -> list[dict[str, Any]]:
        return list(self.get("/api/v1/incidents", limit=200))

    def incident(self, incident_id: str) -> dict[str, Any]:
        return dict(self.get(f"/api/v1/incidents/{incident_id}"))

    def action(self, action_id: str) -> dict[str, Any]:
        return dict(self.get(f"/api/v1/actions/{action_id}"))

    def run(self, run_id: str) -> dict[str, Any]:
        return dict(self.get(f"/api/v1/investigations/{run_id}"))

    def decide(self, action: dict[str, Any], decision: str, reason: str) -> int:
        body: dict[str, Any] = {"expected_version": action["version"], "reason": reason}
        if decision == "approve":
            body["action_hash"] = action["action_hash"]
        response = self.client.post(
            f"{self.base_url}/api/v1/actions/{action['id']}/{decision}", json=body
        )
        return response.status_code
