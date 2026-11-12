"""Policy evaluation for proposed actions.

Input is a normalized request; output is ``ALLOW``, ``DENY`` or
``REQUIRES_APPROVAL`` with a machine-readable reason code and the policy
version. Evaluation errors deny (fail closed).
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from incidentops.domain.catalog import ServiceCatalog
from incidentops.domain.schemas import ArgumentValue, Environment, TypeId

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "policy.yaml"


class Decision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"


class ReasonCode(StrEnum):
    APPROVAL_REQUIRED = "approval_required"
    UNKNOWN_OPERATION = "unknown_operation"
    PRODUCTION_DISABLED = "production_disabled"
    ENVIRONMENT_NOT_ALLOWED = "environment_not_allowed"
    TARGET_NOT_ALLOWLISTED = "target_not_allowlisted"
    ARGUMENTS_NOT_ALLOWED = "arguments_not_allowed"
    MISSING_TARGET_REVISION = "missing_target_revision"
    POLICY_ERROR = "policy_error"
    DUPLICATE_ACTIVE_ACTION = "duplicate_active_action"
    ACTION_NOT_ALLOWED_FOR_ROOT_CAUSE = "action_not_allowed_for_root_cause"
    CONFIDENCE_BELOW_MINIMUM = "confidence_below_minimum"


_CONFIDENCE_RANK: Final = {"low": 0, "medium": 1, "high": 2}


class PolicyRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_type: TypeId
    environments: tuple[Environment, ...] = Field(min_length=1)
    decision: Literal[Decision.DENY, Decision.REQUIRES_APPROVAL]
    risk_class: Literal["low", "medium", "high"]
    allowed_arguments: dict[TypeId, tuple[ArgumentValue, ...]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _never_production(self) -> Self:
        if Environment.PRODUCTION in self.environments:
            raise ValueError(f"{self.action_type}: production remediation is not supported")
        return self


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    policy_version: TypeId
    approval_ttl_seconds: Annotated[int, Field(ge=60, le=3600)]
    require_separation_of_duties: bool
    rules: tuple[PolicyRule, ...]
    # Diagnosed root cause -> the only action types that may remediate it
    # (ADR-024). Missing root causes allow nothing.
    root_cause_actions: dict[TypeId, tuple[TypeId, ...]] = Field(default_factory=dict)
    # Lowest diagnosis confidence that may still lead to an approval request
    # (ADR-025). "medium": low-confidence diagnoses never get an action.
    minimum_action_confidence: Literal["low", "medium", "high"] = "medium"

    @model_validator(mode="after")
    def _unique_rules(self) -> Self:
        names = [r.action_type for r in self.rules]
        if len(names) != len(set(names)):
            raise ValueError("duplicate action_type rules")
        unknown = {
            action
            for actions in self.root_cause_actions.values()
            for action in actions
            if action not in names
        }
        if unknown:
            raise ValueError(f"root_cause_actions name actions without a rule: {sorted(unknown)}")
        return self

    def actions_for_root_cause(self, root_cause_id: str) -> tuple[str, ...]:
        return tuple(self.root_cause_actions.get(root_cause_id, ()))

    def digest(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


class PolicyRequest(BaseModel):
    """Normalized tuple from architecture §9.1."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    principal: str
    action_type: str
    environment: str
    target_id: str
    arguments: dict[str, Any]
    target_revision: str | None
    # The diagnosis the proposal remediates. The proposal evaluator always
    # sets it; the executor's pre-dispatch re-check passes None because the
    # diagnosis is immutable and the policy version is pinned in the envelope.
    root_cause_id: str | None = None
    # The diagnosis' confidence_level; None on the executor's re-check, for the
    # same reason as root_cause_id.
    confidence_level: str | None = None


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision: Decision
    reason_code: ReasonCode
    policy_version: str
    risk_class: str | None = None


class PolicyEngine:
    def __init__(self, config: PolicyConfig, catalog: ServiceCatalog) -> None:
        self.config = config
        self._catalog = catalog
        self._rules = {rule.action_type: rule for rule in config.rules}

    @property
    def version(self) -> str:
        return self.config.policy_version

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        try:
            return self._evaluate(request)
        except Exception:
            # Fail closed on any evaluation error (architecture §15).
            return self._deny(ReasonCode.POLICY_ERROR)

    def _deny(self, reason: ReasonCode) -> PolicyDecision:
        return PolicyDecision(
            decision=Decision.DENY, reason_code=reason, policy_version=self.version
        )

    def _evaluate(self, request: PolicyRequest) -> PolicyDecision:
        if request.environment == Environment.PRODUCTION.value:
            return self._deny(ReasonCode.PRODUCTION_DISABLED)
        rule = self._rules.get(request.action_type)
        if rule is None:
            return self._deny(ReasonCode.UNKNOWN_OPERATION)
        if request.environment not in {e.value for e in rule.environments}:
            return self._deny(ReasonCode.ENVIRONMENT_NOT_ALLOWED)
        if request.root_cause_id is not None and (
            request.action_type not in self.config.actions_for_root_cause(request.root_cause_id)
        ):
            return self._deny(ReasonCode.ACTION_NOT_ALLOWED_FOR_ROOT_CAUSE)
        if (
            request.confidence_level is not None
            and _CONFIDENCE_RANK.get(request.confidence_level, -1)
            < _CONFIDENCE_RANK[self.config.minimum_action_confidence]
        ):
            # Unknown levels rank below everything (fail closed).
            return self._deny(ReasonCode.CONFIDENCE_BELOW_MINIMUM)
        if not self._catalog.allows_action(request.target_id, request.action_type):
            return self._deny(ReasonCode.TARGET_NOT_ALLOWLISTED)
        target = self._catalog.get(request.target_id)
        if target.environment.value != request.environment:
            return self._deny(ReasonCode.ENVIRONMENT_NOT_ALLOWED)
        if set(request.arguments) != set(rule.allowed_arguments):
            return self._deny(ReasonCode.ARGUMENTS_NOT_ALLOWED)
        for name, value in request.arguments.items():
            allowed = rule.allowed_arguments[name]
            # Compare with type: True must not match 1 and False must not match 0.
            if not any(type(value) is type(a) and value == a for a in allowed):
                return self._deny(ReasonCode.ARGUMENTS_NOT_ALLOWED)
        if not request.target_revision:
            return self._deny(ReasonCode.MISSING_TARGET_REVISION)
        if rule.decision is Decision.DENY:
            return self._deny(ReasonCode.UNKNOWN_OPERATION)
        return PolicyDecision(
            decision=Decision.REQUIRES_APPROVAL,
            reason_code=ReasonCode.APPROVAL_REQUIRED,
            policy_version=self.version,
            risk_class=rule.risk_class,
        )


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> PolicyConfig:
    return PolicyConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
