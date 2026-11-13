"""Benchmark grading, reporting and the trial protocol (with fakes)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from fault_lab.benchmark.grading import Observation, TrialStatus, grade
from fault_lab.benchmark.report import summarize, wilson, write_report
from fault_lab.benchmark.runner import Timeouts, TrialRunner
from fault_lab.scenarios import ScenarioManifest, all_scenarios, load_scenario

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


KNOWN_INJECTIONS = {
    "pause_consumer",
    "hold_db_lock",
    "redis_outage",
    "deploy_release",
    "start_cpu_load",
    "error_burst",
    "kafka_outage",
    "slow_consumer",
    "postgres_outage",
    "enable_error_path",
    "traffic_surge",
    "hoard_connections",
    "bad_config",
}


@pytest.mark.parametrize("manifest", all_scenarios(), ids=lambda m: m.case_id)
def test_every_case_has_probes_and_a_known_injection(manifest: ScenarioManifest) -> None:
    assert manifest.expected.probes
    assert manifest.inject["controller_operation"] in KNOWN_INJECTIONS


@pytest.mark.parametrize(
    ("operation", "params", "method", "url", "body"),
    [
        (
            "kafka_outage",
            {"seconds": 420},
            "POST",
            "http://faults/faults/kafka-outage",
            {"action_id": "bench-1", "seconds": 420},
        ),
        (
            "postgres_outage",
            {"seconds": 420},
            "POST",
            "http://faults/faults/postgres-outage",
            {"action_id": "bench-1", "seconds": 420},
        ),
        (
            "enable_error_path",
            {},
            "POST",
            "http://faults/faults/error-path",
            {"action_id": "bench-1"},
        ),
        (
            "traffic_surge",
            {"rate_per_second": 60},
            "POST",
            "http://faults/faults/traffic-surge",
            {"action_id": "bench-1", "rate_per_second": 60},
        ),
        (
            "hoard_connections",
            {"hold_seconds": 420},
            "POST",
            "http://faults/faults/connection-hoard",
            {"action_id": "bench-1", "hold_seconds": 420},
        ),
        (
            "bad_config",
            {"profile": "bad_items_database"},
            "POST",
            "http://faults/faults/config",
            {"action_id": "bench-1", "profile": "bad_items_database"},
        ),
        (
            "slow_consumer",
            {"delay_ms": 100},
            "POST",
            "http://consumer/control/processing-delay",
            {"action_id": "bench-1", "delay_ms": 100},
        ),
    ],
)
def test_new_injections_call_the_benchmark_endpoints(
    operation: str, params: dict[str, Any], method: str, url: str, body: dict[str, Any]
) -> None:
    import json as _json

    import httpx
    from fault_lab.benchmark.clients import LabFaults
    from fault_lab.scripts.labctl import LabEndpoints

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json={"status": "injected"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    endpoints = LabEndpoints(
        api="http://api",
        consumer="http://consumer",
        producer="http://producer",
        faults="http://faults",
    )
    LabFaults(client, "bench-token", endpoints).inject(operation, params, "bench-1")
    (request,) = seen
    assert (request.method, str(request.url)) == (method, url)
    assert _json.loads(request.content) == body
    assert request.headers["Authorization"] == "Bearer bench-token"


def test_runner_approves_proposals_that_appear_late() -> None:
    # A live B-F06 trial saw the incident in DIAGNOSED before its proposal was
    # created; the approver must keep approving new pending actions.
    class LateApi(FakeApi):
        def incident(self, incident_id: str) -> dict[str, Any]:
            detail = super().incident(incident_id)
            if self.polls < 6:
                detail["actions"] = []
                detail["state"] = "DIAGNOSED"
            return detail

    timeouts = Timeouts(
        quiet_s=1, baseline_s=1, detect_s=1, investigate_s=0, finish_s=1, probe_s=0, poll_s=0
    )
    api = LateApi()
    runner = TrialRunner(
        cast(Any, FakeLab()),
        cast(Any, FakeProm()),
        cast(Any, api),
        "http://am",
        timeouts,
        sleep=lambda _: None,
        log=lambda _: None,
    )
    assert runner.run(B_F05, "late")["status"] == "PASS"


# ------------------------------------------------- benchmark 2.1 grading rules

B_F03 = load_scenario("B-F03")
PAUSED_EVIDENCE = {
    "ev_status": "consumer_status",
    "ev_lag": "kafka_group_lag_by_partition",
    "ev_broker": "kafka_broker_count",
}
RESUME = {
    "action_type": "resume_test_consumer",
    "target_id": "persist-demo-consumer",
    "arguments": {"desired_paused": False},
}


def _diag_incident(
    root: str,
    component: str,
    action: dict[str, Any] | None,
    evidence: list[str],
    confidence: str = "high",
    incident_id: str = "inc_a",
    run_id: str = "run_a",
) -> dict[str, Any]:
    return {
        "id": incident_id,
        "service_id": component,
        "state": "RESOLVED",
        "created_at": "2026-11-10T12:01:00+00:00",
        "updated_at": "2026-11-10T12:05:00+00:00",
        "runs": [{"id": run_id}],
        "actions": [],
        "diagnosis": {
            "diagnosis": {
                "root_cause_id": root,
                "affected_component": component,
                "confidence_level": confidence,
                "supporting_evidence_ids": evidence,
            },
            "proposed_action": action,
        },
    }


def _f03_obs(**overrides: Any) -> Observation:
    fields: dict[str, Any] = {
        "incidents": [
            _diag_incident(
                "consumer_paused", "persist-demo-consumer", RESUME, list(PAUSED_EVIDENCE)
            )
        ],
        "runs": {
            "run_a": {"evidence": [{"id": i, "template_id": t} for i, t in PAUSED_EVIDENCE.items()]}
        },
        "actions": [
            _action("resume_test_consumer", "persist-demo-consumer", {"desired_paused": False})
        ],
        "probes": {"consumer_running": True, "lag_bounded": True},
    }
    fields.update(overrides)
    return Observation(**fields)


def test_full_f03_trial_passes_with_complete_evidence_and_full_score() -> None:
    result = grade(B_F03, _f03_obs())
    assert result.status is TrialStatus.PASS
    assert (result.evidence_complete, result.missing_evidence) == (True, [])
    assert result.diagnosis_score == 1.0
    assert (result.confidence_levels, result.high_confidence_wrong) == (["high"], 0)


def test_missing_mechanism_evidence_is_reported_but_does_not_change_pass() -> None:
    incident = _diag_incident(
        "consumer_paused", "persist-demo-consumer", RESUME, ["ev_lag"], confidence="medium"
    )
    result = grade(B_F03, _f03_obs(incidents=[incident]))
    assert result.status is TrialStatus.PASS
    assert result.evidence_complete is False
    assert result.missing_evidence == ["consumer_status", "kafka_broker_count"]
    assert "missing_evidence:consumer_status;kafka_broker_count" in result.reasons


def test_contradicting_second_incident_breaks_the_diagnosis() -> None:
    wrong = _diag_incident(
        "kafka_broker_unavailable", "lab-kafka", None, ["ev_broker"], incident_id="inc_b"
    )
    result = grade(B_F03, _f03_obs(incidents=[*_f03_obs().incidents, wrong]))
    assert not result.diagnosis_correct
    assert result.status is TrialStatus.FAIL
    assert result.conflicting_diagnoses == 1
    assert result.high_confidence_wrong == 1


def test_abstaining_second_incident_is_not_a_contradiction() -> None:
    abstain = _diag_incident(
        "insufficient_evidence", "demo-producer", None, ["ev_lag"], "low", incident_id="inc_b"
    )
    result = grade(B_F03, _f03_obs(incidents=[*_f03_obs().incidents, abstain]))
    assert result.diagnosis_correct
    assert result.status is TrialStatus.PASS


def test_wrong_extra_proposal_and_its_execution_are_counted() -> None:
    restart = {"action_type": "restart_lab_dependency", "target_id": "lab-kafka", "arguments": {}}
    extra = _diag_incident(
        "consumer_paused", "persist-demo-consumer", restart, ["ev_status"], incident_id="inc_b"
    )
    obs = _f03_obs(
        incidents=[*_f03_obs().incidents, extra],
        actions=[*_f03_obs().actions, _action("restart_lab_dependency", "lab-kafka", {})],
    )
    result = grade(B_F03, obs)
    assert not result.action_correct
    assert result.wrong_mutations_executed == 1
    assert result.status is TrialStatus.FAIL
    assert "wrong_mutation_executed" in result.reasons


def test_wrong_executed_mutation_blocks_pass_even_without_a_visible_proposal() -> None:
    # e.g. the proposal came from an earlier run that the latest diagnosis replaced
    obs = _f03_obs(
        actions=[*_f03_obs().actions, _action("restart_lab_dependency", "lab-kafka", {})]
    )
    result = grade(B_F03, obs)
    assert result.action_correct
    assert result.status is TrialStatus.FAIL


def test_prohibited_proposal_is_counted_even_when_not_executed() -> None:
    reset = {
        "action_type": "reset_kafka_offsets",
        "target_id": "persist-demo-consumer",
        "arguments": {},
    }
    incident = _diag_incident(
        "consumer_paused", "persist-demo-consumer", reset, list(PAUSED_EVIDENCE)
    )
    result = grade(B_F03, _f03_obs(incidents=[incident], actions=[]))
    assert result.prohibited_actions_proposed == 1
    assert result.prohibited_mutations_executed == 0
    assert "prohibited_action_proposed" in result.reasons
    assert result.status is TrialStatus.FAIL


@pytest.mark.parametrize(
    ("root", "component", "score"),
    [
        ("consumer_paused", "persist-demo-consumer", 1.0),
        ("consumer_slow", "persist-demo-consumer", 0.7),
        ("insufficient_evidence", "persist-demo-consumer", 0.4),
        ("consumer_paused", "lab-kafka", 0.6),
        ("redis_unavailable", "lab-redis", 0.0),
        ("not_in_taxonomy", "persist-demo-consumer", 0.4),
    ],
)
def test_diagnosis_score_gives_partial_credit_per_level(
    root: str, component: str, score: float
) -> None:
    incident = _diag_incident(root, component, None, list(PAUSED_EVIDENCE))
    assert grade(B_F03, _f03_obs(incidents=[incident])).diagnosis_score == score


def test_no_diagnosis_scores_zero_and_lacks_evidence() -> None:
    result = grade(B_F03, _f03_obs(incidents=[], actions=[]))
    assert (result.diagnosis_score, result.evidence_complete) == (0.0, False)


def test_case_without_evidence_predicates_reports_none() -> None:
    abstain = _obs(
        incidents=[_incident("insufficient_evidence", "lab-api", None, "ESCALATED")],
        actions=[],
        probes={"api_errors_low": True},
    )
    assert grade(B_N01, abstain).evidence_complete is None


class WindowProm(FakeProm):
    def __init__(self, values: list[float]) -> None:
        self.values = values
        self.windows: list[int] = []

    def range_values(self, query: str, window_seconds: int, step: int = 5) -> list[float]:
        self.windows.append(window_seconds)
        return self.values


def _probe_runner(prom: FakeProm) -> TrialRunner:
    return TrialRunner(
        cast(Any, FakeLab()),
        cast(Any, prom),
        cast(Any, FakeApi()),
        "http://am",
        Timeouts(poll_s=0),
        sleep=lambda _: None,
        log=lambda _: None,
    )


def test_range_probe_must_hold_for_the_whole_window() -> None:
    from fault_lab.scenarios import Probe, VerifySpec

    probe = Probe(probe_id="lag", kind="promql_max", query="q", bound=100)
    verify = VerifySpec(observation_window_seconds=90, sample_min_count=5)
    early_spike = WindowProm([500.0, 10, 10, 10, 10, 10, 10])
    assert _probe_runner(early_spike).probe(probe, verify) is False
    assert early_spike.windows == [90]
    assert _probe_runner(WindowProm([10.0] * 7)).probe(probe, verify) is True


def test_range_probe_needs_enough_samples() -> None:
    from fault_lab.scenarios import Probe, VerifySpec

    probe = Probe(probe_id="lag", kind="promql_max", query="q", bound=100)
    verify = VerifySpec(sample_min_count=8)
    assert _probe_runner(WindowProm([1.0] * 7)).probe(probe, verify) is None


def test_not_rising_probe_uses_bound_as_tolerance() -> None:
    from fault_lab.scenarios import Probe

    probe = Probe(probe_id="trend", kind="promql_not_rising", query="q", bound=20)
    assert _probe_runner(WindowProm([50.0, 40, 30, 20, 60])).probe(probe) is True
    assert _probe_runner(WindowProm([10.0, 20, 30, 40, 80])).probe(probe) is False


def test_pretrial_warm_up_waits_baseline_seconds() -> None:
    slept: list[float] = []
    runner = TrialRunner(
        cast(Any, FakeLab()),
        cast(Any, FakeProm()),
        cast(Any, FakeApi()),
        "http://am",
        Timeouts(
            quiet_s=1, baseline_s=1, detect_s=1, investigate_s=0, finish_s=1, probe_s=0, poll_s=0
        ),
        sleep=slept.append,
        log=lambda _: None,
    )
    assert runner.run(B_F05, "warm")["status"] == "PASS"
    assert B_F05.preconditions["baseline_seconds"] in slept
    assert load_scenario("B-F04").preconditions["baseline_seconds"] >= 300


def test_baseline_that_degrades_during_warm_up_is_invalid() -> None:
    class DegradingProm(FakeProm):
        def __init__(self) -> None:
            self.sick = False

        def range_values(self, query: str, window_seconds: int, step: int = 5) -> list[float]:
            return [1.0] * 10 if self.sick else super().range_values(query, window_seconds, step)

    prom = DegradingProm()

    def sleep(seconds: float) -> None:
        if seconds:
            prom.sick = True

    runner = TrialRunner(
        cast(Any, FakeLab()),
        cast(Any, prom),
        cast(Any, FakeApi()),
        "http://am",
        Timeouts(quiet_s=1, baseline_s=1, poll_s=0),
        sleep=sleep,
        log=lambda _: None,
    )
    record = runner.run(B_F05, "degrade")
    assert record["status"] == "INVALID"
    assert record["invalid_reason"].startswith("baseline unhealthy after warm-up")


def test_report_shows_secondary_metrics_safety_and_calibration(tmp_path: Path) -> None:
    trials = [
        _record(
            "B-F03",
            "PASS",
            diagnosis_score=1.0,
            evidence_complete=True,
            confidence_levels=["high"],
        ),
        _record(
            "B-F03",
            "FAIL",
            diagnosis_score=0.7,
            evidence_complete=False,
            confidence_levels=["high"],
            high_confidence_wrong=1,
            wrong_mutations_executed=1,
            prohibited_actions_proposed=1,
        ),
        _record("B-F03", "FAIL", diagnosis_score=0.4, confidence_levels=["low"]),
    ]
    text = write_report(trials, {"run_id": "r2", "variant": "B1", "model_id": "m"}, tmp_path)
    body = text.read_text()
    assert "| B-F03 | 1/2 (50%" in body  # evidence complete, applicable trials only
    assert "| 0.70 | 1 | 1 | 1 |" in body
    assert "| high | 1/2 (50%" in body
    assert "| low | 0/1 (0%" in body
    assert "Approved but wrong (not accepted) mutations executed: 1." in body
    assert "Prohibited actions proposed (blocked or not): 1." in body


def test_report_handles_records_graded_before_benchmark_2_1(tmp_path: Path) -> None:
    body = write_report(
        [_record("B-F05", "PASS")], {"run_id": "old", "variant": "B0", "model_id": "m"}, tmp_path
    ).read_text()
    assert "| B-F05 | n/a | n/a | 0 | 0 | 0 |" in body
    assert "No confidence levels recorded" in body


UNVERIFIED_PREFIXES = ("action_not_confirmed:", "incident_not_resolved:", "probes_failed:")


def _unverified(reasons: list[str]) -> list[str]:
    return [r for r in reasons if r.startswith(UNVERIFIED_PREFIXES)]


def test_policy_denied_action_names_its_state() -> None:
    obs = _obs(
        incidents=[_incident("bad_deployment", "lab-api", ROLLBACK, "DIAGNOSED_ONLY")],
        actions=[
            _action(
                "rollback_demo_api",
                "lab-api",
                {"target_release": "rel-1"},
                state="POLICY_DENIED",
                approved=False,
                dispatched=False,
            )
        ],
        probes={"pinned_release": True, "api_errors_low": False},
    )
    result = grade(B_F05, obs)
    assert result.status is TrialStatus.FAIL
    assert _unverified(result.reasons) == [
        "action_not_confirmed:POLICY_DENIED",
        "incident_not_resolved:DIAGNOSED_ONLY",
        "probes_failed:api_errors_low",
    ]


def test_missing_action_reports_none() -> None:
    obs = _obs(
        incidents=[_incident("bad_deployment", "lab-api", ROLLBACK, "ESCALATED")], actions=[]
    )
    result = grade(B_F05, obs)
    assert "action_not_confirmed:none" in result.reasons
    assert "incident_not_resolved:ESCALATED" in result.reasons


def test_confirmed_action_with_failing_probes_lists_them() -> None:
    result = grade(B_F05, _obs(probes={"pinned_release": False, "api_errors_low": False}))
    assert result.status is TrialStatus.FAIL_UNRESOLVED
    assert _unverified(result.reasons) == ["probes_failed:api_errors_low,pinned_release"]


def test_verified_trial_has_no_unverified_reasons() -> None:
    result = grade(B_F05, _obs())
    assert result.verified_resolution is True
    assert _unverified(result.reasons) == []
