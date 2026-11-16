"""The root-cause definitions draft (P8) carries no benchmark ground truth.

The text is meant for the investigation prompt, so it must not name a case or
contain any string that only a scenario manifest holds (injection details,
probes, expected evidence). When it moves into the prompt, point this test at
the prompt constant instead.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from incidentops.agent.prompt import ROOT_CAUSE_TAXONOMY

ROOT = Path(__file__).resolve().parents[2]
DRAFT = ROOT / "docs" / "proposals" / "2026-11-16-root-cause-definitions.md"
SCENARIOS = ROOT / "fault-lab" / "scenarios"


def _definitions() -> str:
    text = DRAFT.read_text(encoding="utf-8")
    return text.split("<!-- root-cause-definitions:start -->")[1].split(
        "<!-- root-cause-definitions:end -->"
    )[0]


def _strings(node: Any) -> set[str]:
    if isinstance(node, dict):
        return {s for v in node.values() for s in _strings(v)}
    if isinstance(node, list):
        return {s for v in node for s in _strings(v)}
    return {str(node)} if isinstance(node, str | int | float) else set()


def _manifest_only_strings() -> set[str]:
    """Injection, probe and expected-evidence strings of every scenario."""
    found: set[str] = set()
    for path in SCENARIOS.glob("B-*.yaml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        faults = raw.get("faults") or [{"inject": raw["inject"], "expected": raw["expected"]}]
        for fault in faults:
            found |= _strings(fault["inject"])
            expected = fault["expected"]
            for probe in expected.get("probes", []):
                found |= {probe["probe_id"], *(_strings(probe.get("query")))}
            for requirement in expected.get("required_evidence", []):
                found |= {*requirement["any_of"], requirement["why"]}
    # Short or generic values (numbers, "faulty") are not distinctive, and the
    # root-cause names are already part of the prompt.
    return {
        s for s in found if len(s) >= 8 and not s.isdigit() and s not in set(ROOT_CAUSE_TAXONOMY)
    }


def test_definitions_cover_every_root_cause_once() -> None:
    lines = [ln for ln in _definitions().splitlines() if ln.startswith("- ")]
    named = [ln[2:].split(":", 1)[0] for ln in lines]
    assert named == list(ROOT_CAUSE_TAXONOMY)


def test_definitions_name_no_benchmark_case() -> None:
    assert not re.search(r"\bB-[FN]\d\d\b", _definitions())


def test_definitions_contain_no_manifest_only_strings() -> None:
    text = _definitions().lower()
    leaked = sorted(s for s in _manifest_only_strings() if s.lower() in text)
    assert leaked == []
