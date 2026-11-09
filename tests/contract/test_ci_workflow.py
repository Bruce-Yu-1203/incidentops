"""Guard rails for the CI workflow (benchmark.md §13 CI policy)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict[str, Any]:
    return dict(yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def test_ci_has_least_privilege_permissions() -> None:
    assert _workflow()["permissions"] == {"contents": "read"}


def test_ci_never_receives_lab_or_model_secrets() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text
    assert "FAULT_LAB_BENCHMARK_TOKEN" not in text


def test_ci_runs_python_web_and_compose_jobs() -> None:
    assert set(_workflow()["jobs"]) == {"python", "web", "compose"}


def test_ci_requires_database_for_integration_tests() -> None:
    env = _workflow()["jobs"]["python"]["env"]
    assert env["INCIDENTOPS_REQUIRE_DB"] == "1"
