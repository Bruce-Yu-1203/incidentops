"""Versioned scenario manifests (benchmark.md §3), benchmark controller only.

``ScenarioManifest.agent_view()`` is the only projection that may ever be
handed to IncidentOps; it deliberately drops injection details and ground
truth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SCENARIO_DIR = Path(__file__).resolve().parents[1] / "scenarios"
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AcceptedAction(_Strict):
    type: Identifier
    target_id: Identifier
    arguments: dict[str, str | int | bool | None] = Field(default_factory=dict)


class VerifySpec(_Strict):
    """How the grader's independent probes judge recovery (benchmark.md §6.3).

    Every probe of the case must hold for *all* samples of the observation
    window, and the window must contain at least ``sample_min_count`` samples.
    ``recovery_timeout_seconds`` bounds how long the grader waits for that.
    Cases without a ``verify`` block use the defaults below.
    """

    observation_window_seconds: Annotated[int, Field(gt=0, le=600)] = 60
    sample_min_count: Annotated[int, Field(gt=0)] = 5
    recovery_timeout_seconds: Annotated[int, Field(gt=0)] = 120


class Probe(_Strict):
    """Independent postcondition probe run by the grader (never by IncidentOps).

    PromQL is allowed here because probes live on the benchmark side. Range
    probes are evaluated over every sample of the case's observation window:
    ``promql_max`` needs every sample <= ``bound``, ``promql_min`` needs every
    sample >= ``bound`` and ``promql_not_rising`` needs the last sample to be
    no higher than the first plus ``bound`` (a tolerance, default 0).
    """

    probe_id: Identifier
    kind: Literal["promql_max", "promql_min", "promql_not_rising", "consumer_running", "release_is"]
    query: str | None = None
    bound: float | None = None
    value: str | None = None


class EvidenceRequirement(_Strict):
    """One deterministic evidence predicate (benchmark.md §6.1).

    The diagnosis must cite at least one evidence record produced by any of
    the listed evidence templates (the ``template_id`` of the record).
    """

    any_of: list[Identifier] = Field(min_length=1)
    why: str


class Expected(_Strict):
    accepted_root_cause_ids: list[Identifier] = Field(min_length=1)
    affected_component: Identifier
    accepted_actions: list[AcceptedAction]
    prohibited_actions: list[Identifier] = Field(default_factory=list)
    allow_no_action: bool
    requires_human_approval: bool
    verify: VerifySpec = Field(default_factory=VerifySpec)
    probes: list[Probe] = Field(default_factory=list)
    required_evidence: list[EvidenceRequirement] = Field(default_factory=list)


class AgentVisible(_Strict):
    service_id: Identifier
    environment: Literal["fault-lab"]
    alert_labels: dict[str, str]


class ScenarioManifest(_Strict):
    schema_version: Literal[1]
    case_id: Annotated[str, StringConstraints(pattern=r"^B-[FN]\d{2}$")]
    case_version: Annotated[int, Field(ge=1)]
    name: Identifier
    lab_profile: Identifier
    seed: int
    preconditions: dict[str, Any]
    inject: dict[str, Any]
    trigger: dict[str, Any]
    agent_visible: AgentVisible
    expected: Expected
    cleanup: dict[str, Any]

    def agent_view(self) -> dict[str, Any]:
        """The only scenario data that may be shared with IncidentOps."""
        return {"case_id": self.case_id, **self.agent_visible.model_dump()}

    def leaks_ground_truth(self) -> list[str]:
        """Return ground-truth tokens that appear in the agent-visible view."""
        visible = yaml.safe_dump(self.agent_view()).lower()
        secrets = [*self.expected.accepted_root_cause_ids, self.inject.get("controller_operation")]
        return [token for token in secrets if token and str(token).lower() in visible]


def load_scenario(case_id: str, directory: Path = SCENARIO_DIR) -> ScenarioManifest:
    path = directory / f"{case_id}.yaml"
    return ScenarioManifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def all_scenarios(directory: Path = SCENARIO_DIR) -> list[ScenarioManifest]:
    return [load_scenario(p.stem, directory) for p in sorted(directory.glob("B-*.yaml"))]
