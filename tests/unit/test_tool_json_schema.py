"""Tool schemas sent to models carry no $ref/$defs (provider compatibility)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from incidentops.agent.prompt import submit_diagnosis_tool
from incidentops.chat.agent import answer_tool, get_evidence_tool
from incidentops.tools.json_schema import inline_refs
from tests.unit.tool_fakes import make_gateway


def _all_tools() -> list[dict[str, Any]]:
    gateway, _, _, _ = make_gateway()
    return [*gateway.manifest(), submit_diagnosis_tool(), get_evidence_tool(), answer_tool()]


@pytest.mark.parametrize("tool", _all_tools(), ids=lambda t: str(t["name"]))
def test_no_tool_schema_contains_references(tool: dict[str, Any]) -> None:
    text = json.dumps(tool["input_schema"])
    assert "$ref" not in text
    assert "$defs" not in text


def test_submit_diagnosis_nests_the_diagnosis_object() -> None:
    schema = submit_diagnosis_tool()["input_schema"]
    assert schema["required"] == ["diagnosis"]
    diagnosis = schema["properties"]["diagnosis"]
    assert diagnosis["type"] == "object"
    assert "root_cause_id" in diagnosis["properties"]
    assert "incident_id" not in schema["properties"]


def test_refs_are_inlined_and_sibling_keywords_win() -> None:
    schema = {
        "$defs": {"Level": {"type": "string", "enum": ["low", "high"], "description": "d"}},
        "type": "object",
        "properties": {
            "level": {"$ref": "#/$defs/Level", "description": "override"},
            "maybe": {"anyOf": [{"$ref": "#/$defs/Level"}, {"type": "null"}]},
        },
    }
    assert inline_refs(schema) == {
        "type": "object",
        "properties": {
            "level": {"type": "string", "enum": ["low", "high"], "description": "override"},
            "maybe": {
                "anyOf": [
                    {"type": "string", "enum": ["low", "high"], "description": "d"},
                    {"type": "null"},
                ]
            },
        },
    }


def test_recursive_and_external_references_are_rejected() -> None:
    recursive = {
        "$defs": {"N": {"properties": {"next": {"$ref": "#/$defs/N"}}}},
        "$ref": "#/$defs/N",
    }
    with pytest.raises(ValueError, match="recursive"):
        inline_refs(recursive)
    with pytest.raises(ValueError, match="unsupported"):
        inline_refs({"$ref": "https://example.com/schema.json"})


def test_every_submit_diagnosis_field_is_described() -> None:
    # ADR-028: models misplaced fields; each field says what it is and where it lives.
    schema = submit_diagnosis_tool()["input_schema"]
    action = next(
        s for s in schema["properties"]["proposed_action"]["anyOf"] if s.get("type") == "object"
    )
    for name, prop in [
        *schema["properties"].items(),
        *schema["properties"]["diagnosis"]["properties"].items(),
        *action["properties"].items(),
    ]:
        assert prop.get("description"), f"{name} has no description"
    assert "not inside it" in schema["properties"]["needs_human_review"]["description"]
    assert "never a JSON-encoded string" in schema["properties"]["diagnosis"]["description"]


def test_tool_description_gives_the_top_level_shape() -> None:
    description = submit_diagnosis_tool()["description"]
    assert "exactly three top-level fields" in description
    shape = description.split("Shape (IDs and values are placeholders): ")[1]
    top = json.loads(shape)
    assert set(top) == {"diagnosis", "proposed_action", "needs_human_review"}
    assert "needs_human_review" not in top["diagnosis"]


def test_root_cause_enum_is_the_taxonomy_and_matches_policy() -> None:
    from incidentops.agent.prompt import ROOT_CAUSE_TAXONOMY
    from incidentops.policy.engine import load_policy

    schema = submit_diagnosis_tool()["input_schema"]
    enum = schema["properties"]["diagnosis"]["properties"]["root_cause_id"]["enum"]
    assert enum == list(ROOT_CAUSE_TAXONOMY)
    assert "insufficient_evidence" in enum
    # Every root cause the model may name has an entry in the policy contract.
    assert set(enum) == set(load_policy().root_cause_actions)


def test_component_and_action_enums_come_from_catalog_and_policy() -> None:
    from incidentops.domain.catalog import load_catalog
    from incidentops.policy.engine import load_policy

    catalog, policy = load_catalog(), load_policy()
    schema = submit_diagnosis_tool()["input_schema"]
    diagnosis = schema["properties"]["diagnosis"]["properties"]
    assert diagnosis["affected_component"]["enum"] == sorted(s.service_id for s in catalog.services)
    action = next(
        s for s in schema["properties"]["proposed_action"]["anyOf"] if s.get("type") == "object"
    )["properties"]
    assert action["action_type"]["enum"] == sorted(r.action_type for r in policy.rules)
    assert set(action["target_id"]["enum"]) <= {s.service_id for s in catalog.services}


def test_arguments_are_a_closed_set_with_per_action_contracts() -> None:
    arguments = next(
        s
        for s in submit_diagnosis_tool()["input_schema"]["properties"]["proposed_action"]["anyOf"]
        if s.get("type") == "object"
    )["properties"]["arguments"]
    assert arguments["additionalProperties"] is False
    assert arguments["properties"]["desired_paused"] == {"enum": [False]}
    assert arguments["properties"]["target_release"] == {"enum": ["rel-1"]}
    # B-F04 r5 left target_release out; the contract is stated in the schema itself,
    # in the one_of form (a bare list was copied as the value before).
    assert (
        '"rollback_demo_api": {"target_release": {"one_of": ["rel-1"]}}'
        in (arguments["description"])
    )


def test_tool_schema_holds_no_scenario_ground_truth() -> None:
    text = json.dumps(submit_diagnosis_tool())
    for leak in ("expected", "accepted_root_cause_ids", "prohibited_actions", "B-F", "B-N"):
        assert leak not in text
