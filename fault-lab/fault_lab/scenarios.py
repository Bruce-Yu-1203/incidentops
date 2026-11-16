"""Versioned scenario manifests (benchmark.md §3), benchmark controller only.

``ScenarioManifest.agent_view()`` is the only projection that may ever be
handed to IncidentOps; it deliberately drops injection details and ground
truth.

Schema 1 describes one fault (top-level ``inject`` and ``expected``). Schema 2
(benchmark 2.2) lists several ``faults`` injected together, each with its own
``inject``, ``expected`` and ``expected_group``. ``fault_list()`` gives both
schemas the same shape.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

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


class ExpectedGroup(_Strict):
    """The incident group a fault should form (benchmark 2.2, ADR-030).

    ``primary`` is the service whose incident should be the group primary (or,
    for a split-out fault, the incident that diagnoses it). ``may_include``
    lists services whose incidents may join because the fault reaches them;
    downstream services do not alert in every trial.
    """

    primary: Identifier
    may_include: list[Identifier] = Field(default_factory=list)

    def services(self) -> set[str]:
        return {self.primary, *self.may_include}


class Fault(_Strict):
    fault_id: Identifier
    inject: dict[str, Any]
    expected: Expected
    expected_group: ExpectedGroup | None = None

    def group(self) -> ExpectedGroup:
        return self.expected_group or ExpectedGroup(primary=self.expected.affected_component)


class AgentVisible(_Strict):
    service_id: Identifier
    environment: Literal["fault-lab"]
    alert_labels: dict[str, str]


class ScenarioManifest(_Strict):
    schema_version: Literal[1, 2]
    case_id: Annotated[str, StringConstraints(pattern=r"^B-[FN]\d{2}$")]
    case_version: Annotated[int, Field(ge=1)]
    name: Identifier
    lab_profile: Identifier
    seed: int
    preconditions: dict[str, Any]
    trigger: dict[str, Any]
    agent_visible: AgentVisible
    cleanup: dict[str, Any]
    # Schema 1: one fault.
    inject: dict[str, Any] | None = None
    expected: Expected | None = None
    expected_group: ExpectedGroup | None = None
    # Schema 2: several faults injected together.
    faults: list[Fault] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape_matches_schema(self) -> Self:
        if self.schema_version == 1:
            if self.inject is None or self.expected is None or self.faults:
                raise ValueError("schema 1 needs inject and expected, and no faults")
        else:
            if self.inject is not None or self.expected is not None or self.expected_group:
                raise ValueError("schema 2 puts inject, expected and groups under faults")
            ids = [f.fault_id for f in self.faults]
            if len(ids) < 2 or len(set(ids)) != len(ids):
                raise ValueError("schema 2 needs at least two faults with unique fault_id")
        return self

    def fault_list(self) -> list[Fault]:
        """Every fault of the trial; a schema 1 manifest has exactly one."""
        if self.faults:
            return list(self.faults)
        if self.inject is None or self.expected is None:  # pragma: no cover - validated
            raise ValueError("schema 1 manifest without inject or expected")
        group = self.expected_group or ExpectedGroup(primary=self.agent_visible.service_id)
        return [
            Fault(fault_id="main", inject=self.inject, expected=self.expected, expected_group=group)
        ]

    def agent_view(self) -> dict[str, Any]:
        """The only scenario data that may be shared with IncidentOps."""
        return {"case_id": self.case_id, **self.agent_visible.model_dump()}

    def leaks_ground_truth(self) -> list[str]:
        """Return ground-truth tokens that appear in the agent-visible view."""
        visible = yaml.safe_dump(self.agent_view()).lower()
        secrets: list[Any] = []
        for fault in self.fault_list():
            secrets += [
                *fault.expected.accepted_root_cause_ids,
                fault.inject.get("controller_operation"),
            ]
        return [token for token in secrets if token and str(token).lower() in visible]


def load_scenario(case_id: str, directory: Path = SCENARIO_DIR) -> ScenarioManifest:
    path = directory / f"{case_id}.yaml"
    return ScenarioManifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def all_scenarios(directory: Path = SCENARIO_DIR) -> list[ScenarioManifest]:
    return [load_scenario(p.stem, directory) for p in sorted(directory.glob("B-*.yaml"))]
