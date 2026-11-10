"""Deterministic policy engine tests (FR-019, S-01, S-02, S-08, S-09)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from incidentops.domain.catalog import ServiceCatalog, load_catalog
from incidentops.policy.engine import (
    Decision,
    PolicyConfig,
    PolicyEngine,
    PolicyRequest,
    ReasonCode,
    load_policy,
)


def _engine() -> PolicyEngine:
    return PolicyEngine(load_policy(), load_catalog())


def _request(**overrides: Any) -> PolicyRequest:
    fields: dict[str, Any] = {
        "principal": "service:proposal-evaluator",
        "action_type": "resume_test_consumer",
        "environment": "fault-lab",
        "target_id": "persist-demo-consumer",
        "arguments": {"desired_paused": False},
        "target_revision": "gen-8",
    }
    fields.update(overrides)
    return PolicyRequest(**fields)


def test_resume_requires_human_approval() -> None:
    decision = _engine().evaluate(_request())
    assert decision.decision is Decision.REQUIRES_APPROVAL
    assert decision.reason_code is ReasonCode.APPROVAL_REQUIRED
    assert decision.policy_version == "p2"
    assert decision.risk_class == "low"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"environment": "production"}, ReasonCode.PRODUCTION_DISABLED),  # S-01
        ({"action_type": "delete_topic"}, ReasonCode.UNKNOWN_OPERATION),
        ({"action_type": "run_shell"}, ReasonCode.UNKNOWN_OPERATION),  # S-02
        ({"environment": "staging"}, ReasonCode.ENVIRONMENT_NOT_ALLOWED),
        ({"target_id": "lab-api"}, ReasonCode.TARGET_NOT_ALLOWLISTED),  # S-08
        ({"target_id": "10.0.0.5"}, ReasonCode.TARGET_NOT_ALLOWLISTED),
        ({"arguments": {"desired_paused": True}}, ReasonCode.ARGUMENTS_NOT_ALLOWED),
        ({"arguments": {"desired_paused": 0}}, ReasonCode.ARGUMENTS_NOT_ALLOWED),
        ({"arguments": {}}, ReasonCode.ARGUMENTS_NOT_ALLOWED),
        (
            {"arguments": {"desired_paused": False, "offset": "earliest"}},
            ReasonCode.ARGUMENTS_NOT_ALLOWED,
        ),
        ({"target_revision": None}, ReasonCode.MISSING_TARGET_REVISION),
    ],
)
def test_denials(overrides: dict[str, Any], reason: ReasonCode) -> None:
    decision = _engine().evaluate(_request(**overrides))
    assert (decision.decision, decision.reason_code) == (Decision.DENY, reason)


def test_evaluation_errors_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # S-09: a broken policy dependency denies instead of allowing.
    engine = _engine()

    def boom(*_: Any) -> bool:
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(ServiceCatalog, "allows_action", boom)
    decision = engine.evaluate(_request())
    assert (decision.decision, decision.reason_code) == (Decision.DENY, ReasonCode.POLICY_ERROR)


def _raw_policy(**rule_overrides: Any) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "action_type": "resume_test_consumer",
        "environments": ["fault-lab"],
        "decision": "REQUIRES_APPROVAL",
        "risk_class": "low",
        "allowed_arguments": {"desired_paused": [False]},
    }
    rule.update(rule_overrides)
    return {
        "schema_version": 1,
        "policy_version": "p1",
        "approval_ttl_seconds": 900,
        "require_separation_of_duties": True,
        "rules": [rule],
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"decision": "ALLOW"},  # writes never run without approval
        {"environments": ["production"]},
        {"environments": []},
        {"unexpected": True},
    ],
)
def test_invalid_policy_configs_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PolicyConfig.model_validate(_raw_policy(**overrides))


def test_duplicate_rules_rejected() -> None:
    raw = _raw_policy()
    raw["rules"].append(dict(raw["rules"][0]))
    with pytest.raises(ValidationError, match="duplicate"):
        PolicyConfig.model_validate(raw)


def test_policy_digest_changes_with_content() -> None:
    a = PolicyConfig.model_validate(_raw_policy())
    b = PolicyConfig.model_validate(_raw_policy(risk_class="high"))
    assert a.digest() != b.digest()
    assert a.digest() == PolicyConfig.model_validate(_raw_policy()).digest()


def test_explicit_deny_rule() -> None:
    config = PolicyConfig.model_validate(_raw_policy(decision="DENY"))
    decision = PolicyEngine(config, load_catalog()).evaluate(_request())
    assert decision.decision is Decision.DENY


@pytest.mark.parametrize(
    ("action_type", "target_id", "arguments", "allowed"),
    [
        ("release_lab_db_blocker", "lab-postgres", {}, True),
        ("restart_lab_dependency", "lab-redis", {}, True),
        ("rollback_demo_api", "lab-api", {"target_release": "rel-1"}, True),
        ("rollback_demo_api", "lab-api", {"target_release": "rel-7"}, False),
        ("rollback_demo_api", "lab-api", {"image": "attacker/api:latest"}, False),
        ("stop_lab_cpu_load", "lab-cpu-load", {}, True),
        ("restart_lab_dependency", "lab-postgres", {}, False),  # wrong target
        ("release_lab_db_blocker", "lab-postgres", {"pid": 4242}, False),
    ],
)
def test_p5_remediations(
    action_type: str, target_id: str, arguments: dict[str, Any], allowed: bool
) -> None:
    decision = _engine().evaluate(
        _request(action_type=action_type, target_id=target_id, arguments=arguments)
    )
    assert (decision.decision is Decision.REQUIRES_APPROVAL) is allowed
