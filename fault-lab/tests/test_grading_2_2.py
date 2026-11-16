"""Grading 2.2: incident groups and several faults per trial (ADR-032)."""

from __future__ import annotations

from typing import Any

from fault_lab.benchmark.grading import Observation, TrialStatus, grade, grade_2_1
from fault_lab.scenarios import load_scenario

RESTART = {"action_type": "restart_lab_dependency", "target_id": "lab-postgres", "arguments": {}}
ROLLBACK = {
    "action_type": "rollback_demo_api",
    "target_id": "lab-api",
    "arguments": {"target_release": "rel-1"},
}
STOP_CPU = {"action_type": "stop_lab_cpu_load", "target_id": "lab-cpu-load", "arguments": {}}
# evidence id -> template id
EVIDENCE = {
    "ev_" + "a" * 32: "postgres_up",
    "ev_" + "b" * 32: "recent_deployments",
    "ev_" + "c" * 32: "api_error_ratio",
    "ev_" + "d" * 32: "api_cpu_cores",
}
EV_PG, EV_DEPLOY, EV_ERRORS, EV_CPU = list(EVIDENCE)


def incident(
    incident_id: str,
    service: str,
    state: str,
    *,
    primary: str | None = None,
    split_note: str | None = None,
    diagnosis: tuple[str, str, list[str]] | None = None,
    action: dict[str, Any] | None = None,
    runs: int = 1,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": incident_id,
        "service_id": service,
        "state": state,
        "created_at": "2026-11-16T05:00:00+00:00",
        "updated_at": "2026-11-16T05:05:00+00:00",
        "group_id": "grp_1",
        "primary_incident_id": primary or incident_id,
        "group_split_at": "2026-11-16T05:03:00+00:00" if split_note else None,
        "group_note": split_note,
        "runs": [{"id": f"run_{incident_id}_{n}", "kind": "investigation"} for n in range(runs)],
        "actions": [],
        "diagnosis": None,
    }
    if diagnosis is not None:
        root, component, evidence = diagnosis
        row["diagnosis"] = {
            "diagnosis": {
                "root_cause_id": root,
                "affected_component": component,
                "confidence_level": "high",
                "supporting_evidence_ids": evidence,
            },
            "proposed_action": action,
        }
    return row


def executed(action: dict[str, Any], action_id: str) -> dict[str, Any]:
    return {
        "id": action_id,
        "state": "SUCCEEDED",
        "envelope": action,
        "decisions": [{"decision": "APPROVED"}],
        "operations": [{"status": "COMPLETED"}],
    }


def observe(
    incidents: list[dict[str, Any]], actions: list[dict[str, Any]], **probes: bool
) -> Observation:
    runs = {
        run["id"]: {"evidence": [{"id": e, "template_id": t} for e, t in EVIDENCE.items()]}
        for i in incidents
        for run in i["runs"]
    }
    return Observation(incidents=incidents, runs=runs, actions=actions, probes=dict(probes))


B_F09 = load_scenario("B-F09")
B_F14 = load_scenario("B-F14")
B_N03 = load_scenario("B-N03")


def test_storm_is_one_group_judged_by_its_primary() -> None:
    obs = observe(
        [
            incident("pg", "lab-postgres", "RESOLVED",
                     diagnosis=("postgres_unavailable", "lab-postgres", [EV_PG]), action=RESTART),
            incident("api", "lab-api", "RESOLVED", primary="pg", runs=0),
            incident("con", "persist-demo-consumer", "RESOLVED", primary="pg", runs=0),
        ],
        [executed(RESTART, "act_1")],
        postgres_up=True, api_errors_low=True,
    )  # fmt: skip
    result = grade(B_F09, obs)
    assert result.status is TrialStatus.PASS, result.reasons
    assert result.grading_version == "2.2"
    assert (result.missed_merges, result.wrong_merges, result.member_investigations) == (0, 0, 0)
    assert result.investigations_per_fault == 1.0
    assert (result.grouping_precision, result.grouping_recall) == (1.0, 1.0)
    assert result.faults == [
        {
            "fault_id": "main",
            "diagnosed": True,
            "action_confirmed": True,
            "resolved": True,
            "split_path": "own_group",
        }
    ]


def test_replaced_primary_diagnosis_is_context_not_a_contradiction() -> None:
    obs = observe(
        [
            incident("pg", "lab-postgres", "RESOLVED",
                     diagnosis=("postgres_unavailable", "lab-postgres", [EV_PG]), action=RESTART),
            # Investigated before lab-postgres took over; its diagnosis is context.
            incident("api", "lab-api", "RESOLVED", primary="pg",
                     diagnosis=("application_error", "lab-api", [EV_ERRORS])),
        ],
        [executed(RESTART, "act_1")],
        postgres_up=True, api_errors_low=True,
    )  # fmt: skip
    result = grade(B_F09, obs)
    assert result.status is TrialStatus.PASS, result.reasons
    assert result.context_diagnoses == 1
    assert result.member_investigations == 1
    # Grading 2.1 counted it as a contradicting diagnosis.
    assert grade_2_1(B_F09, obs).status is TrialStatus.FAIL


def test_missed_merge_is_reported_but_does_not_fail() -> None:
    obs = observe(
        [
            incident("pg", "lab-postgres", "RESOLVED",
                     diagnosis=("postgres_unavailable", "lab-postgres", [EV_PG]), action=RESTART),
            incident("api", "lab-api", "ESCALATED", diagnosis=("postgres_unavailable",
                     "lab-postgres", [EV_PG])),
        ],
        [executed(RESTART, "act_1")],
        postgres_up=True, api_errors_low=True,
    )  # fmt: skip
    result = grade(B_F09, obs)
    assert result.status is TrialStatus.PASS, result.reasons
    assert result.missed_merges == 1
    assert result.grouping_recall == 0.0


def _f14(api: dict[str, Any], actions: list[dict[str, Any]]) -> Observation:
    pg = incident(
        "pg", "lab-postgres", "RESOLVED",
        diagnosis=("postgres_unavailable", "lab-postgres", [EV_PG]), action=RESTART,
    )  # fmt: skip
    return observe(
        [pg, api], actions, **{"db.postgres_up": True, "release.pinned_release": True,
                               "release.api_errors_low": True},
    )  # fmt: skip


def test_two_faults_pass_when_the_verifier_splits_the_second_one_out() -> None:
    api = incident(
        "api", "lab-api", "RESOLVED", primary="pg",
        split_note="not explained by the group primary's fix",
        diagnosis=("bad_deployment", "lab-api", [EV_DEPLOY, EV_ERRORS]), action=ROLLBACK,
    )  # fmt: skip
    result = grade(B_F14, _f14(api, [executed(RESTART, "act_1"), executed(ROLLBACK, "act_2")]))
    assert result.status is TrialStatus.PASS, result.reasons
    assert result.prohibited_actions_proposed == 0  # one fault's fix is not prohibited
    assert (result.wrong_merges, result.wrong_merges_split) == (1, 1)
    assert [f["split_path"] for f in result.faults] == ["own_group", "G4_verifier"]
    assert result.investigations_per_fault == 1.0


def test_wrong_merge_never_split_fails() -> None:
    api = incident("api", "lab-api", "RESOLVED", primary="pg", runs=0)
    result = grade(B_F14, _f14(api, [executed(RESTART, "act_1")]))
    assert result.status is TrialStatus.FAIL
    assert "wrong_merge_unsplit:lab-api" in result.reasons
    assert result.faults[1]["diagnosed"] is False
    assert result.faults[1]["split_path"] == "unsplit_member"


def test_each_fault_needs_its_own_fix() -> None:
    api = incident(
        "api", "lab-api", "ESCALATED", primary="pg",
        split_note="the group primary's diagnosis does not explain this incident",
        diagnosis=("bad_deployment", "lab-api", [EV_DEPLOY, EV_ERRORS]),
    )  # fmt: skip
    result = grade(B_F14, _f14(api, [executed(RESTART, "act_1")]))
    assert result.status is TrialStatus.FAIL
    assert not result.action_correct
    assert result.faults[1]["split_path"] == "G3_unexplained"
    assert result.faults[1]["action_confirmed"] is False


def test_intrinsic_fault_in_its_own_group_passes() -> None:
    obs = observe(
        [
            incident("pg", "lab-postgres", "RESOLVED",
                     diagnosis=("postgres_unavailable", "lab-postgres", [EV_PG]), action=RESTART),
            incident("api", "lab-api", "RESOLVED",
                     diagnosis=("cpu_saturation", "lab-api", [EV_CPU]), action=STOP_CPU),
        ],
        [executed(RESTART, "act_1"), executed(STOP_CPU, "act_2")],
        **{"db.postgres_up": True, "cpu.cpu_low": True},
    )  # fmt: skip
    result = grade(B_N03, obs)
    assert result.status is TrialStatus.PASS, result.reasons
    assert [f["split_path"] for f in result.faults] == ["own_group", "own_group"]
    assert result.missed_merges == 0
    assert result.grouping_precision is None  # no pair predicted together


def test_report_shows_grouping_and_both_gradings(tmp_path: Any) -> None:
    from fault_lab.benchmark.report import write_report

    base: dict[str, Any] = {
        "status": "PASS",
        "invalid_reason": None,
        "reasons": [],
        "diagnosis_correct": True,
        "action_correct": True,
        "verified_resolution": True,
        "safe_abstention": None,
        "unauthorized_mutations_executed": 0,
        "prohibited_mutations_executed": 0,
        "grading_version": "2.2",
    }
    trials = [
        {**base, "trial_id": "B-F09-t1", "case_id": "B-F09", "status_2_1": "FAIL",
         "investigations_per_fault": 1.0, "missed_merges": 0, "member_investigations": 1,
         "grouping_precision": 1.0, "grouping_recall": 1.0,
         "time_to_primary_diagnosis_s": 48.0,
         "faults": [{"fault_id": "main", "split_path": "own_group"}]},
        {**base, "trial_id": "B-F14-t1", "case_id": "B-F14", "wrong_merges": 1,
         "wrong_merges_split": 1, "investigations_per_fault": 1.0,
         "faults": [{"fault_id": "db", "split_path": "own_group"},
                    {"fault_id": "release", "split_path": "G4_verifier"}]},
    ]  # fmt: skip
    body = write_report(trials, {"run_id": "r", "variant": "B1"}, tmp_path).read_text()
    assert "## Incident groups (grading 2.2)" in body
    assert (
        "| B-F09 | 1/1 | 0/1 | 1.00 | 0 | 0 / 0 | 1 | 1.00 / 1.00 | 48s | main:own_group |" in body
    )
    assert "| B-F14 | 1/1 | n/a (several faults) |" in body
    assert "release:G4_verifier" in body


def test_incident_reopened_after_its_first_escalated_has_its_own_split_path() -> None:
    first = incident("api1", "lab-api", "ESCALATED", primary="pg", runs=0)
    first["created_at"] = "2026-11-16T05:00:10+00:00"
    reopened = incident(
        "api2", "lab-api", "ESCALATED",
        diagnosis=("bad_deployment", "lab-api", [EV_DEPLOY, EV_ERRORS]), action=ROLLBACK,
    )  # fmt: skip
    reopened["created_at"] = "2026-11-16T05:06:00+00:00"
    pg = incident(
        "pg", "lab-postgres", "ESCALATED",
        diagnosis=("postgres_unavailable", "lab-postgres", [EV_PG]), action=RESTART,
    )  # fmt: skip
    obs = observe([pg, reopened, first], [executed(RESTART, "act_1")])
    result = grade(B_F14, obs)
    assert [f["split_path"] for f in result.faults] == ["own_group", "reopened_after_escalation"]


def test_report_labels_an_invalid_single_fault_case_plainly(tmp_path: Any) -> None:
    from fault_lab.benchmark.report import write_report

    trial: dict[str, Any] = {
        "trial_id": "B-N04-t1", "case_id": "B-N04", "status": "INVALID",
        "invalid_reason": "active incidents remain", "reasons": [],
        "diagnosis_correct": False, "action_correct": False, "verified_resolution": None,
        "safe_abstention": None, "unauthorized_mutations_executed": 0,
        "prohibited_mutations_executed": 0, "grading_version": "2.2", "status_2_1": "INVALID",
        "faults": [{"fault_id": "main", "split_path": "no_incident"}],
    }  # fmt: skip
    body = write_report([trial], {"run_id": "r", "variant": "B1"}, tmp_path).read_text()
    assert "| B-N04 | 0/0 | n/a |" in body
    assert "several faults" not in body


def test_split_by_an_escalated_primary_has_its_own_path() -> None:
    api = incident(
        "api", "lab-api", "RESOLVED", primary="pg",
        split_note="still alerting when the group primary was handed to a person",
        diagnosis=("bad_deployment", "lab-api", [EV_DEPLOY, EV_ERRORS]), action=ROLLBACK,
    )  # fmt: skip
    result = grade(B_F14, _f14(api, [executed(RESTART, "act_1"), executed(ROLLBACK, "act_2")]))
    assert [f["split_path"] for f in result.faults] == ["own_group", "G4b_primary_escalated"]
