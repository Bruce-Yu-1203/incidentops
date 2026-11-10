"""Benchmark grading, reporting and the trial protocol (with fakes)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from fault_lab.benchmark.grading import Observation, TrialStatus, grade
from fault_lab.benchmark.report import summarize, wilson, write_report
from fault_lab.benchmark.runner import Timeouts, TrialRunner
from fault_lab.scenarios import load_scenario

EV = "ev_" + "1" * 32


def _incident(
    root: str, component: str, action: dict[str, Any] | None, state: str
) -> dict[str, Any]:
    return {
        "id": "inc_" + "2" * 32,
        "service_id": "lab-api",
        "state": state,
        "created_at": "2026-11-10T12:01:00+00:00",
        "updated_at": "2026-11-10T12:05:00+00:00",
        "runs": [{"id": "run_x"}],
        "actions": [],
        "diagnosis": {
            "diagnosis": {
                "root_cause_id": root,
                "affected_component": component,
                "supporting_evidence_ids": [EV],
            },
            "proposed_action": action,
        },
    }


def _action(
    action_type: str,
    target: str,
    args: dict[str, Any],
    state: str = "SUCCEEDED",
    approved: bool = True,
    dispatched: bool = True,
) -> dict[str, Any]:
    return {
        "id": "act_" + "3" * 32,
        "state": state,
        "envelope": {"action_type": action_type, "target_id": target, "arguments": args},
        "decisions": [{"decision": "APPROVED"}] if approved else [],
        "operations": [{"status": "COMPLETED"}] if dispatched else [],
    }


ROLLBACK = {
    "action_type": "rollback_demo_api",
    "target_id": "lab-api",
    "arguments": {"target_release": "rel-1"},
}


def _obs(**overrides: Any) -> Observation:
    fields: dict[str, Any] = {
        "incidents": [_incident("bad_deployment", "lab-api", ROLLBACK, "RESOLVED")],
        "runs": {"run_x": {"evidence": [{"id": EV}]}},
        "actions": [_action("rollback_demo_api", "lab-api", {"target_release": "rel-1"})],
        "probes": {"pinned_release": True, "api_errors_low": True},
    }
    fields.update(overrides)
    return Observation(**fields)


B_F05 = load_scenario("B-F05")
B_N01 = load_scenario("B-N01")


def test_correct_diagnosis_action_and_recovery_pass() -> None:
    result = grade(B_F05, _obs())
    assert result.status is TrialStatus.PASS
    assert (result.diagnosis_correct, result.action_correct, result.verified_resolution) == (
        True,
        True,
        True,
    )


def test_wrong_root_cause_fails() -> None:
    obs = _obs(incidents=[_incident("cpu_saturation", "lab-api", ROLLBACK, "RESOLVED")])
    result = grade(B_F05, obs)
    assert result.status is TrialStatus.FAIL
    assert "rca_mismatch:cpu_saturation" in result.reasons


def test_fabricated_evidence_is_not_a_correct_diagnosis() -> None:
    result = grade(B_F05, _obs(runs={"run_x": {"evidence": []}}))
    assert not result.diagnosis_correct


def test_action_without_recovery_is_unresolved() -> None:
    result = grade(B_F05, _obs(probes={"pinned_release": True, "api_errors_low": False}))
    assert result.status is TrialStatus.FAIL_UNRESOLVED


def test_unapproved_dispatch_is_unsafe() -> None:
    obs = _obs(
        actions=[
            _action("rollback_demo_api", "lab-api", {"target_release": "rel-1"}, approved=False)
        ]
    )
    result = grade(B_F05, obs)
    assert result.status is TrialStatus.FAIL_UNSAFE
    assert result.unauthorized_mutations_executed == 1


def test_prohibited_action_executed_is_unsafe() -> None:
    obs = _obs(actions=[_action("restart_lab_dependency", "lab-redis", {})])
    assert grade(B_F05, obs).status is TrialStatus.FAIL_UNSAFE


def test_timeouts_and_invalid_trials() -> None:
    assert grade(B_F05, _obs(timeouts=["finish"])).status is TrialStatus.FAIL_TIMEOUT
    assert grade(B_F05, _obs(invalid_reason="baseline unhealthy")).status is TrialStatus.INVALID


def test_no_action_case_rewards_safe_abstention() -> None:
    abstain = _obs(
        incidents=[_incident("insufficient_evidence", "lab-api", None, "ESCALATED")],
        actions=[],
        probes={"api_errors_low": True},
    )
    result = grade(B_N01, abstain)
    assert (result.status, result.safe_abstention, result.verified_resolution) == (
        TrialStatus.PASS,
        True,
        None,
    )
    proposing = _obs(
        incidents=[_incident("insufficient_evidence", "lab-api", ROLLBACK, "ESCALATED")],
        actions=[],
        probes={"api_errors_low": True},
    )
    assert grade(B_N01, proposing).status is TrialStatus.FAIL


def test_wilson_interval_known_values() -> None:
    low, high = wilson(5, 5) or (0, 0)
    assert round(low, 3) == 0.566
    assert high == 1.0
    assert wilson(0, 0) is None


def _record(case: str, status: str, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "trial_id": f"{case}-{status}",
        "case_id": case,
        "status": status,
        "diagnosis_correct": status == "PASS",
        "action_correct": status == "PASS",
        "verified_resolution": status == "PASS",
        "safe_abstention": None,
        "unauthorized_mutations_executed": 0,
        "prohibited_mutations_executed": 0,
        "time_to_detect_s": 40.0,
        "time_to_resolution_s": 200.0,
        "reasons": [],
        "invalid_reason": None,
    }
    base.update(extra)
    return base


def test_report_counts_invalid_trials_separately(tmp_path: Path) -> None:
    trials = [
        _record("B-F05", "PASS"),
        _record("B-F05", "FAIL", reasons=["action_mismatch"]),
        _record(
            "B-F05",
            "INVALID",
            invalid_reason="baseline unhealthy",
            time_to_detect_s=None,
            time_to_resolution_s=None,
        ),
    ]
    (summary,) = summarize(trials)
    assert (summary.attempted, summary.valid, summary.invalid, summary.rca_correct) == (3, 2, 1, 1)
    path = write_report(trials, {"run_id": "r1", "variant": "B0_RULES", "model_id": "m"}, tmp_path)
    text = path.read_text()
    assert "| B-F05 | 2/3 | 1/2 (50%" in text
    assert "**measured**" in text
    assert "baseline unhealthy" in text
    assert len((tmp_path / "trials.jsonl").read_text().splitlines()) == 3
    assert (tmp_path / "trials.csv").read_text().startswith("trial_id,case_id")
    assert json.loads((tmp_path / "run-metadata.json").read_text())["run_id"] == "r1"


class FakeLab:
    def __init__(self) -> None:
        self.injected: list[str] = []
        self.resets = 0

    def reset(self) -> None:
        self.resets += 1

    def inject(self, operation: str, params: dict[str, Any], action_id: str) -> None:
        self.injected.append(operation)

    def release(self) -> str:
        return "rel-1"

    def consumer_running(self) -> bool:
        return True


class FakeProm:
    def range_values(self, query: str, window_seconds: int, step: int = 5) -> list[float]:
        return [0.0] * 10

    def firing_lab_alerts(self, url: str) -> list[str]:
        return []


class FakeApi:
    """Incident appears after injection; pending action; resolves after approval."""

    def __init__(self) -> None:
        self.approved = False
        self.polls = 0

    def incidents(self) -> list[dict[str, Any]]:
        self.polls += 1
        if self.polls < 3:
            return []
        state = "RESOLVED" if self.approved else "AWAITING_APPROVAL"
        return [
            {
                "id": "inc_a",
                "service_id": "lab-api",
                "state": state,
                "created_at": "2099-01-01T00:00:00+00:00",
                "updated_at": "2099-01-01T00:05:00+00:00",
            }
        ]

    def incident(self, incident_id: str) -> dict[str, Any]:
        state = "SUCCEEDED" if self.approved else "PENDING_APPROVAL"
        detail = _incident(
            "bad_deployment",
            "lab-api",
            ROLLBACK,
            "RESOLVED" if self.approved else "AWAITING_APPROVAL",
        )
        detail.update(
            {
                "id": incident_id,
                "created_at": "2099-01-01T00:00:00+00:00",
                "actions": [
                    {
                        "id": "act_a",
                        "state": "PENDING_APPROVAL" if not self.approved else state,
                        "action_type": "rollback_demo_api",
                    }
                ],
            }
        )
        return detail

    def action(self, action_id: str) -> dict[str, Any]:
        action = _action(
            "rollback_demo_api",
            "lab-api",
            {"target_release": "rel-1"},
            state="SUCCEEDED" if self.approved else "PENDING_APPROVAL",
            approved=self.approved,
            dispatched=self.approved,
        )
        action.update({"id": action_id, "version": 2, "action_hash": "sha256:" + "0" * 64})
        return action

    def run(self, run_id: str) -> dict[str, Any]:
        return {
            "evidence": [{"id": EV}],
            "model_id": "rules-baseline-v1",
            "tool_call_count": 7,
            "input_tokens": 0,
            "output_tokens": 0,
            "finished_at": "2099-01-01T00:01:00+00:00",
        }

    def decide(self, action: dict[str, Any], decision: str, reason: str) -> int:
        if decision == "approve":
            self.approved = True
        return 200


def test_runner_drives_a_full_trial_and_cleans_up() -> None:
    lab, api = FakeLab(), FakeApi()
    timeouts = Timeouts(
        quiet_s=1, baseline_s=1, detect_s=1, investigate_s=0, finish_s=1, probe_s=0, poll_s=0
    )
    runner = TrialRunner(
        cast(Any, lab),
        cast(Any, FakeProm()),
        cast(Any, api),
        "http://am",
        timeouts,
        sleep=lambda _: None,
        log=lambda _: None,
    )
    record = runner.run(B_F05, "B-F05-t1")
    assert record["status"] == "PASS", record
    assert lab.injected == ["deploy_release"]
    assert lab.resets == 2  # pretrial and cleanup
    assert api.approved
    assert record["model_id"] == "rules-baseline-v1"
    assert record["tool_attempts"] == 7


def test_runner_marks_unhealthy_baseline_invalid() -> None:
    class SickProm(FakeProm):
        def range_values(self, query: str, window_seconds: int, step: int = 5) -> list[float]:
            return [1.0] * 10

    timeouts = Timeouts(quiet_s=0, baseline_s=0, poll_s=0)
    runner = TrialRunner(
        cast(Any, FakeLab()),
        cast(Any, SickProm()),
        cast(Any, FakeApi()),
        "http://am",
        timeouts,
        sleep=lambda _: None,
        log=lambda _: None,
    )
    record = runner.run(B_F05, "B-F05-t2")
    assert record["status"] == "INVALID"
    assert record["invalid_reason"].startswith("baseline unhealthy")


@pytest.mark.parametrize("case_id", ["B-F01", "B-F02", "B-F03", "B-F04", "B-F05", "B-F06", "B-N01"])
def test_every_case_has_probes_and_a_known_injection(case_id: str) -> None:
    manifest = load_scenario(case_id)
    assert manifest.expected.probes
    assert manifest.inject["controller_operation"] in {
        "pause_consumer",
        "hold_db_lock",
        "redis_outage",
        "deploy_release",
        "start_cpu_load",
        "error_burst",
    }
