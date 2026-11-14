"""Standard trial protocol (benchmark.md §4).

reset -> pretrial checks -> warm-up (``baseline_seconds``) -> inject -> detect -> investigate ->
authorize (seeded approver, same API as humans) -> execute/verify (by
IncidentOps) -> independent probes -> grade -> cleanup.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fault_lab.benchmark.clients import IncidentOpsApi, LabFaults, Prometheus
from fault_lab.benchmark.grading import TERMINAL, Observation, TrialResult, grade
from fault_lab.scenarios import Probe, ScenarioManifest, VerifySpec

LAB_SERVICES = {
    "lab-api",
    "lab-postgres",
    "lab-redis",
    "lab-cpu-load",
    "persist-demo-consumer",
    "demo-producer",
    "lab-kafka",
    "lab-loadgen",
    "lab-api-config",
}
# DIAGNOSED is transient: a proposal is still being policy-evaluated.
IN_PROGRESS = {"RECEIVED", "TRIAGED", "INVESTIGATING", "DIAGNOSED"}


@dataclass(frozen=True)
class Timeouts:
    quiet_s: float = 300
    baseline_s: float = 180
    detect_s: float = 240
    investigate_s: float = 420
    finish_s: float = 480
    # None: wait up to the case's ``verify.recovery_timeout_seconds``.
    probe_s: float | None = None
    poll_s: float = 5


class TrialAbortedError(RuntimeError):
    """A pretrial or harness invariant failed: the trial is INVALID."""


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 1)


class TrialRunner:
    def __init__(
        self,
        lab: LabFaults,
        prometheus: Prometheus,
        api: IncidentOpsApi,
        alertmanager_url: str,
        timeouts: Timeouts | None = None,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
    ) -> None:
        self._lab = lab
        self._prom = prometheus
        self._api = api
        self._alertmanager = alertmanager_url
        self._t = timeouts or Timeouts()
        self._sleep = sleep
        self._log = log

    # ---------------------------------------------------------------- helpers

    def _wait(self, predicate: Callable[[], Any], seconds: float) -> Any:
        deadline = time.monotonic() + seconds
        while True:
            value = predicate()
            if value or time.monotonic() >= deadline:
                return value
            self._sleep(self._t.poll_s)

    def probe(self, probe: Probe, verify: VerifySpec | None = None) -> bool | None:
        """Evaluate one probe; None means it could not be judged (counts as not passing).

        Range probes must hold for every sample of the observation window, and
        the window must hold at least ``sample_min_count`` samples
        (benchmark.md §6.3: postconditions hold across the required window).
        """
        verify = verify or VerifySpec()
        try:
            if probe.kind == "consumer_running":
                return self._lab.consumer_running()
            if probe.kind == "release_is":
                return self._lab.release() == probe.value
            values = self._prom.range_values(probe.query or "", verify.observation_window_seconds)
        except Exception:
            return None
        if len(values) < verify.sample_min_count:
            return None
        if probe.kind == "promql_not_rising":
            return values[-1] <= values[0] + (probe.bound or 0.0)
        if probe.bound is None:
            return None
        if probe.kind == "promql_max":
            return max(values) <= probe.bound
        return min(values) >= probe.bound

    def _probes(self, manifest: ScenarioManifest) -> dict[str, bool | None]:
        verify = manifest.expected.verify
        return {p.probe_id: self.probe(p, verify) for p in manifest.expected.probes}

    def _active_lab_incidents(self) -> list[dict[str, Any]]:
        return [
            i
            for i in self._api.incidents()
            if i["service_id"] in LAB_SERVICES and i["state"] not in TERMINAL
        ]

    # --------------------------------------------------------------- protocol

    def _pretrial(self, manifest: ScenarioManifest) -> None:
        self._lab.reset()
        # Leftover proposals from earlier trials are rejected so that the next
        # alert opens a fresh incident (one active incident per service key).
        for incident in self._active_lab_incidents():
            detail = self._api.incident(incident["id"])
            for summary in detail.get("actions", []):
                if summary["state"] == "PENDING_APPROVAL":
                    action = self._api.action(summary["id"])
                    self._api.decide(action, "reject", "benchmark pretrial cleanup")
        if self._wait(lambda: not self._active_lab_incidents(), self._t.quiet_s) is False:
            raise TrialAbortedError("active incidents remain from a previous trial")
        quiet = self._wait(
            lambda: not self._prom.firing_lab_alerts(self._alertmanager), self._t.quiet_s
        )
        if not quiet:
            raise TrialAbortedError("lab alerts still firing before injection")
        healthy = self._wait(
            lambda: all(v is True for v in self._probes(manifest).values()), self._t.baseline_s
        )
        if not healthy:
            raise TrialAbortedError(f"baseline unhealthy: {self._probes(manifest)}")
        # Warm-up: keep the healthy baseline for ``baseline_seconds`` so that
        # look-back windows of the next investigation do not still contain the
        # previous trial's fault (B-F04 lost 3 trials to an old memory peak).
        warmup = float(manifest.preconditions.get("baseline_seconds", 0))
        if warmup > 0:
            self._sleep(warmup)
            if not all(v is True for v in self._probes(manifest).values()):
                raise TrialAbortedError(
                    f"baseline unhealthy after warm-up: {self._probes(manifest)}"
                )

    def _trial_incidents(self, since: datetime) -> list[dict[str, Any]]:
        return [
            i
            for i in self._api.incidents()
            if i["service_id"] in LAB_SERVICES and (_parse(i["created_at"]) or since) >= since
        ]

    def run(self, manifest: ScenarioManifest, trial_id: str) -> dict[str, Any]:
        obs = Observation()
        started_at = datetime.now(UTC)
        self._log(f"[{trial_id}] pretrial")
        try:
            self._pretrial(manifest)
        except TrialAbortedError as exc:
            obs.invalid_reason = str(exc)
            return self._record(manifest, trial_id, obs, started_at, None)
        except Exception as exc:  # harness failure, not a system-under-test failure
            obs.invalid_reason = f"harness_error:{type(exc).__name__}"
            return self._record(manifest, trial_id, obs, started_at, None)

        inject_at = datetime.now(UTC)
        obs.inject_at = inject_at.isoformat()
        operation = str(manifest.inject["controller_operation"])
        self._log(f"[{trial_id}] inject {operation}")
        try:
            self._lab.inject(operation, dict(manifest.inject), f"bench-{uuid.uuid4().hex[:12]}")
        except Exception as exc:
            obs.invalid_reason = f"injection_failed:{type(exc).__name__}"
            return self._record(manifest, trial_id, obs, started_at, inject_at)

        try:
            self._drive(manifest, obs, inject_at, trial_id)
        finally:
            self._log(f"[{trial_id}] cleanup")
            try:
                self._lab.reset()
            except Exception:
                obs.invalid_reason = obs.invalid_reason or "cleanup_failed"
        return self._record(manifest, trial_id, obs, started_at, inject_at)

    def _drive(
        self, manifest: ScenarioManifest, obs: Observation, inject_at: datetime, trial_id: str
    ) -> None:
        detected = self._wait(lambda: self._trial_incidents(inject_at), self._t.detect_s)
        if not detected:
            obs.timeouts.append("detect")
            return
        self._log(f"[{trial_id}] detected {len(detected)} incident(s)")

        def investigated() -> bool:
            current = self._trial_incidents(inject_at)
            return bool(current) and all(i["state"] not in IN_PROGRESS for i in current)

        if not self._wait(investigated, self._t.investigate_s):
            obs.timeouts.append("investigate")

        approved: set[str] = set()

        def approve_pending() -> None:
            # The seeded approver approves pending proposals through the public
            # API, including ones that appear while the trial is in progress.
            for incident in self._trial_incidents(inject_at):
                for summary in self._api.incident(incident["id"]).get("actions", []):
                    if summary["state"] == "PENDING_APPROVAL" and summary["id"] not in approved:
                        action = self._api.action(summary["id"])
                        code = self._api.decide(action, "approve", "benchmark approver")
                        approved.add(summary["id"])
                        self._log(f"[{trial_id}] approve {summary['action_type']} -> {code}")

        def finished() -> bool:
            approve_pending()
            return all(i["state"] in TERMINAL for i in self._trial_incidents(inject_at))

        if not self._wait(finished, self._t.finish_s):
            obs.timeouts.append("finish")

        obs.probes = self._wait(
            lambda: (lambda p: p if all(v is True for v in p.values()) else None)(
                self._probes(manifest)
            ),
            manifest.expected.verify.recovery_timeout_seconds
            if self._t.probe_s is None
            else self._t.probe_s,
        ) or self._probes(manifest)

        for incident in self._trial_incidents(inject_at):
            detail = self._api.incident(incident["id"])
            obs.incidents.append(detail)
            for run in detail.get("runs", []):
                obs.runs[run["id"]] = self._api.run(run["id"])
            for summary in detail.get("actions", []):
                obs.actions.append(self._api.action(summary["id"]))

    def _record(
        self,
        manifest: ScenarioManifest,
        trial_id: str,
        obs: Observation,
        started_at: datetime,
        inject_at: datetime | None,
    ) -> dict[str, Any]:
        result: TrialResult = grade(manifest, obs)
        created = [_parse(i.get("created_at")) for i in obs.incidents]
        first_created = min((c for c in created if c), default=None)
        finished_runs = [_parse(r.get("finished_at")) for r in obs.runs.values()]
        first_diagnosis = min((f for f in finished_runs if f), default=None)
        resolved = [
            _parse(i.get("updated_at")) for i in obs.incidents if i.get("state") == "RESOLVED"
        ]
        record = result.to_json()
        record.update(
            {
                "trial_id": trial_id,
                "case_version": manifest.case_version,
                "seed": manifest.seed,
                "started_at": started_at.isoformat(),
                "inject_at": obs.inject_at,
                "incident_ids": [i["id"] for i in obs.incidents],
                "action_ids": [a["id"] for a in obs.actions],
                "run_ids": list(obs.runs),
                "model_id": ",".join(sorted({str(r.get("model_id")) for r in obs.runs.values()})),
                "time_to_detect_s": _seconds(inject_at, first_created),
                "time_to_diagnosis_s": _seconds(first_created, first_diagnosis),
                "time_to_resolution_s": _seconds(
                    inject_at, min((r for r in resolved if r), default=None)
                ),
                "tool_attempts": sum(int(r.get("tool_call_count", 0)) for r in obs.runs.values()),
                "tokens_input": sum(int(r.get("input_tokens", 0)) for r in obs.runs.values()),
                "tokens_output": sum(int(r.get("output_tokens", 0)) for r in obs.runs.values()),
                # tokens_input is uncached input; cached input is counted apart.
                "tokens_cache_read": sum(
                    int(r.get("cache_read_input_tokens", 0)) for r in obs.runs.values()
                ),
                "tokens_cache_creation": sum(
                    int(r.get("cache_creation_input_tokens", 0)) for r in obs.runs.values()
                ),
                "argument_coercions": sum(
                    int(r.get("argument_coercions", 0)) for r in obs.runs.values()
                ),
                "field_lifts": sum(int(r.get("field_lifts", 0)) for r in obs.runs.values()),
            }
        )
        return record
