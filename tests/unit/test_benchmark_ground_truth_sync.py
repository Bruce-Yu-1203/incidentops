"""Benchmark ground truth stays in sync with the agent's label and evidence spaces."""

from __future__ import annotations

import pytest
from fault_lab.benchmark.taxonomy import ROOT_CAUSES
from fault_lab.scenarios import ScenarioManifest, all_scenarios

from incidentops.agent.prompt import ROOT_CAUSE_TAXONOMY
from incidentops.tools.templates import TEMPLATES

# Evidence template IDs written by tools that are not metric templates.
TOOL_EVIDENCE_TEMPLATES = {
    "consumer_status",
    "postgres_health",
    "recent_config_changes",
    "recent_deployments",
    "redis_health",
    "service_logs",
    "service_traces",
}


def test_grader_taxonomy_covers_every_agent_root_cause() -> None:
    assert set(ROOT_CAUSES) == set(ROOT_CAUSE_TAXONOMY)


@pytest.mark.parametrize("manifest", all_scenarios(), ids=lambda m: m.case_id)
def test_scenario_ground_truth_uses_known_labels(manifest: ScenarioManifest) -> None:
    assert set(manifest.expected.accepted_root_cause_ids) <= set(ROOT_CAUSES)
    known = set(TEMPLATES) | TOOL_EVIDENCE_TEMPLATES
    for requirement in manifest.expected.required_evidence:
        assert set(requirement.any_of) <= known, requirement


@pytest.mark.parametrize("manifest", all_scenarios(), ids=lambda m: m.case_id)
def test_action_cases_declare_evidence_predicates(manifest: ScenarioManifest) -> None:
    if manifest.expected.accepted_root_cause_ids != ["insufficient_evidence"]:
        assert manifest.expected.required_evidence
