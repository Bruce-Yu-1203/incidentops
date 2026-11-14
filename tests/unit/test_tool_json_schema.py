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
