"""Tests for scenario manifests and ground-truth isolation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fault_lab.scenarios import SCENARIO_DIR, ScenarioManifest, all_scenarios, load_scenario
from pydantic import ValidationError


def test_b_f03_manifest_loads() -> None:
    manifest = load_scenario("B-F03")
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
