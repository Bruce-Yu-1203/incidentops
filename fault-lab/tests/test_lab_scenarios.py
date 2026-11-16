"""Tests for scenario manifests and ground-truth isolation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fault_lab.scenarios import SCENARIO_DIR, ScenarioManifest, all_scenarios, load_scenario
from pydantic import ValidationError


def test_b_f03_manifest_loads() -> None:
    manifest = load_scenario("B-F03")
    assert manifest.expected is not None
    assert manifest.expected.accepted_root_cause_ids == ["consumer_paused"]
    assert manifest.expected.requires_human_approval
    assert manifest.expected.accepted_actions[0].type == "resume_test_consumer"


@pytest.mark.parametrize("manifest", all_scenarios(), ids=lambda m: m.case_id)
def test_agent_view_excludes_ground_truth(manifest: ScenarioManifest) -> None:
    view = manifest.agent_view()
    assert set(view) == {"case_id", "service_id", "environment", "alert_labels"}
    assert manifest.leaks_ground_truth() == []


def test_leak_detection_catches_root_cause_in_labels(tmp_path: Path) -> None:
    raw = yaml.safe_load((SCENARIO_DIR / "B-F03.yaml").read_text(encoding="utf-8"))
    raw["agent_visible"]["alert_labels"]["hint"] = "consumer_paused"
    (tmp_path / "B-F03.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert load_scenario("B-F03", tmp_path).leaks_ground_truth() == ["consumer_paused"]


def test_manifest_rejects_unknown_fields(tmp_path: Path) -> None:
    raw = yaml.safe_load((SCENARIO_DIR / "B-F03.yaml").read_text(encoding="utf-8"))
    raw["expected"]["llm_judge"] = True
    (tmp_path / "B-F03.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_scenario("B-F03", tmp_path)


def test_agent_visible_environment_is_lab_only(tmp_path: Path) -> None:
    raw = yaml.safe_load((SCENARIO_DIR / "B-F03.yaml").read_text(encoding="utf-8"))
    raw["agent_visible"]["environment"] = "production"
    (tmp_path / "B-F03.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_scenario("B-F03", tmp_path)


# -- schema 2: several faults per trial (benchmark 2.2) ------------------------------


def test_schema_1_manifest_is_one_fault_with_a_default_group() -> None:
    (fault,) = load_scenario("B-F05").fault_list()
    assert fault.fault_id == "main"
    assert fault.group().primary == "lab-api"
    assert fault.group().may_include == []
    (b_f09,) = load_scenario("B-F09").fault_list()
    assert b_f09.group().services() == {"lab-postgres", "lab-api", "persist-demo-consumer"}


@pytest.mark.parametrize(
    ("case_id", "faults"),
    [
        ("B-F14", {"db": "lab-postgres", "release": "lab-api"}),
        ("B-N03", {"db": "lab-postgres", "cpu": "lab-api"}),
    ],
)
def test_two_fault_cases_load(case_id: str, faults: dict[str, str]) -> None:
    manifest = load_scenario(case_id)
    assert manifest.schema_version == 2
    assert {f.fault_id: f.group().primary for f in manifest.fault_list()} == faults
    accepted = {a.type for f in manifest.fault_list() for a in f.expected.accepted_actions}
    prohibited = {p for f in manifest.fault_list() for p in f.expected.prohibited_actions}
    # One fault's fix is never prohibited by the other.
    assert not accepted & prohibited


def test_control_case_matches_b_f05() -> None:
    control, original = load_scenario("B-N04"), load_scenario("B-F05")
    assert control.schema_version == 1
    assert control.expected == original.expected
    assert control.inject == original.inject


def _raw(case_id: str) -> dict[str, object]:
    return dict(yaml.safe_load((SCENARIO_DIR / f"{case_id}.yaml").read_text(encoding="utf-8")))


def test_schema_1_with_faults_is_rejected() -> None:
    raw = _raw("B-F05")
    raw["faults"] = _raw("B-F14")["faults"]
    with pytest.raises(ValidationError, match="schema 1"):
        ScenarioManifest.model_validate(raw)


def test_schema_2_needs_two_unique_faults_and_no_top_level_expected() -> None:
    raw = _raw("B-F14")
    faults = list(raw["faults"])  # type: ignore[call-overload]
    with pytest.raises(ValidationError, match="at least two faults"):
        ScenarioManifest.model_validate({**raw, "faults": faults[:1]})
    with pytest.raises(ValidationError, match="at least two faults"):
        ScenarioManifest.model_validate({**raw, "faults": [faults[0], faults[0]]})
    with pytest.raises(ValidationError, match="schema 2"):
        ScenarioManifest.model_validate({**raw, "expected": _raw("B-F05")["expected"]})


def test_ground_truth_of_every_fault_stays_out_of_the_agent_view() -> None:
    manifest = load_scenario("B-F14")
    visible = yaml.safe_dump(manifest.agent_view())
    assert "bad_deployment" not in visible
    assert "deploy_release" not in visible
    assert manifest.leaks_ground_truth() == []
