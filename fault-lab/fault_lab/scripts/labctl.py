"""Benchmark-controller CLI for the Fault Lab (test-only privileges).

Usage::

    python -m fault_lab.scripts.labctl status
    python -m fault_lab.scripts.labctl pause
    python -m fault_lab.scripts.labctl reset
    python -m fault_lab.scripts.labctl smoke
    python -m fault_lab.scripts.labctl inject-db-lock [--seconds N]
    python -m fault_lab.scripts.labctl release-db-lock --token TOKEN

``reset`` restores the healthy baseline; ``smoke`` runs the Phase 0 gate:
the consumer progresses when unpaused, stays stationary when paused, recovers
after reset, and non-benchmark roles cannot inject faults.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

METRIC_LINE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+(?P<value>\S+)$")


@dataclass(frozen=True)
class LabEndpoints:
    api: str = os.environ.get("FAULT_LAB_API_URL", "http://127.0.0.1:18080")
    consumer: str = os.environ.get("FAULT_LAB_CONSUMER_URL", "http://127.0.0.1:18081")
    producer: str = os.environ.get("FAULT_LAB_PRODUCER_URL", "http://127.0.0.1:18082")
    faults: str = os.environ.get("FAULT_LAB_FAULTS_URL", "http://127.0.0.1:18090")


class SmokeFailedError(AssertionError):
    """A Phase 0 gate condition did not hold."""


def parse_metric(text: str, name: str) -> float:
    """Return the value of an unlabelled metric sample from exposition text."""
    for line in text.splitlines():
        match = METRIC_LINE.match(line.strip())
        if match and match.group("name") == name:
            return float(match.group("value"))
    raise KeyError(name)


class LabController:
    def __init__(self, endpoints: LabEndpoints, token: str, client: httpx.Client) -> None:
        self.endpoints = endpoints
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client

    def status(self) -> dict[str, Any]:
        response = self._client.get(
            f"{self.endpoints.consumer}/control/status", headers=self._headers
        )
        response.raise_for_status()
        return dict(response.json())

    def pause(self) -> dict[str, Any]:
        generation = self.status()["generation"]
        response = self._client.post(
            f"{self.endpoints.consumer}/control/pause",
            headers=self._headers,
            json={"action_id": _action_id("pause"), "expected_generation": generation},
        )
        response.raise_for_status()
        return dict(response.json())

    def inject_db_lock(self, hold_seconds: float) -> dict[str, Any]:
        response = self._client.post(
            f"{self.endpoints.faults}/faults/db-lock",
            headers=self._headers,
            json={"action_id": _action_id("dblock"), "hold_seconds": hold_seconds},
        )
        response.raise_for_status()
        return dict(response.json())

    def release_db_lock(self, fixture_token: str) -> dict[str, Any]:
        response = self._client.post(
            f"{self.endpoints.faults}/faults/db-lock/release",
            headers=self._headers,
            json={"fixture_token": fixture_token},
        )
        response.raise_for_status()
        return dict(response.json())

    def reset_faults(self) -> dict[str, Any]:
        response = self._client.post(f"{self.endpoints.faults}/faults/reset", headers=self._headers)
        response.raise_for_status()
        return dict(response.json())

    def reset(self) -> dict[str, Any]:
        self.reset_faults()
        response = self._client.post(
            f"{self.endpoints.consumer}/control/reset",
            headers=self._headers,
            json={"action_id": _action_id("reset")},
        )
        response.raise_for_status()
        return dict(response.json())

    def metric(self, base_url: str, name: str) -> float:
        response = self._client.get(f"{base_url}/metrics")
        response.raise_for_status()
        return parse_metric(response.text, name)

    def wait_ready(self, timeout_s: float = 120) -> None:
        deadline = time.monotonic() + timeout_s
        urls = [
            f"{self.endpoints.api}/readyz",
            f"{self.endpoints.consumer}/healthz",
            f"{self.endpoints.producer}/healthz",
            f"{self.endpoints.faults}/healthz",
        ]
        pending = list(urls)
        while pending and time.monotonic() < deadline:
            for url in list(pending):
                try:
                    if self._client.get(url).status_code == 200:
                        pending.remove(url)
                except httpx.HTTPError:
                    pass
            if pending:
                time.sleep(1)
        if pending:
            raise SmokeFailedError(f"services not ready: {pending}")


def _action_id(kind: str) -> str:
    return f"bench-{kind}-{uuid.uuid4().hex[:12]}"


def _processed(lab: LabController) -> float:
    return lab.metric(lab.endpoints.consumer, "lab_consumer_messages_processed_total")


def _sent(lab: LabController) -> float:
    return lab.metric(lab.endpoints.producer, "lab_producer_messages_sent_total")


def _wait_for_progress(lab: LabController, timeout_s: float) -> float:
    start = _processed(lab)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        time.sleep(1)
        current = _processed(lab)
        if current > start:
            return current - start
    raise SmokeFailedError("consumer did not progress while unpaused")


def run_smoke(lab: LabController, client: httpx.Client, other_tokens: dict[str, str]) -> None:
    lab.wait_ready()
    lab.reset()
    _wait_for_progress(lab, timeout_s=60)
    print("ok: consumer progresses when unpaused")

    # Non-benchmark identities (executor, read-only agent/verifier) must not inject.
    for name, token in other_tokens.items():
        response = client.post(
            f"{lab.endpoints.consumer}/control/pause",
            headers={"Authorization": f"Bearer {token}"},
            json={"action_id": _action_id("forbidden"), "expected_generation": "gen-0"},
        )
        if response.status_code != 403:
            raise SmokeFailedError(f"{name} token could pause the consumer: {response.status_code}")
    print("ok: executor and read-only tokens cannot inject faults")

    lab.pause()
    time.sleep(2)  # let any in-flight batch finish
    processed_before, sent_before = _processed(lab), _sent(lab)
    time.sleep(6)
    processed_after, sent_after = _processed(lab), _sent(lab)
    if processed_after != processed_before:
        raise SmokeFailedError("consumer processed messages while paused")
    if sent_after <= sent_before:
        raise SmokeFailedError("producer stopped while consumer was paused")
    if not lab.status()["paused"]:
        raise SmokeFailedError("status does not report paused")
    print(f"ok: consumer stationary while paused (producer +{sent_after - sent_before:.0f})")

    lab.reset()
    if lab.status()["paused"]:
        raise SmokeFailedError("reset did not unpause the consumer")
    _wait_for_progress(lab, timeout_s=30)
    print("ok: consumer healthy again after reset")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["status", "pause", "reset", "smoke", "inject-db-lock", "release-db-lock"],
    )
    parser.add_argument("--seconds", type=float, default=180.0, help="db lock hold time")
    parser.add_argument("--token", help="fixture token to release")
    args = parser.parse_args(argv)
    token = os.environ.get("FAULT_LAB_BENCHMARK_TOKEN")
    if not token:
        print("FAULT_LAB_BENCHMARK_TOKEN is required", file=sys.stderr)
        return 2
    with httpx.Client(timeout=5) as client:
        lab = LabController(LabEndpoints(), token, client)
        try:
            if args.command == "status":
                print(lab.status())
            elif args.command == "pause":
                print(lab.pause())
            elif args.command == "inject-db-lock":
                print(lab.inject_db_lock(args.seconds))
            elif args.command == "release-db-lock":
                if not args.token:
                    print("--token is required", file=sys.stderr)
                    return 2
                print(lab.release_db_lock(args.token))
            elif args.command == "reset":
                lab.wait_ready()
                print(lab.reset())
            else:
                others = {
                    "executor": os.environ.get("FAULT_LAB_EXECUTOR_TOKEN", ""),
                    "reader": os.environ.get("FAULT_LAB_READ_TOKEN", ""),
                }
                run_smoke(lab, client, {k: v for k, v in others.items() if v})
                print("Fault Lab smoke test passed")
        except (SmokeFailedError, httpx.HTTPError) as exc:
            print(f"FAILED: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
