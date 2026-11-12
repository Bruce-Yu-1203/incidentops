"""Unit tests for lag recovery evaluation."""

import pytest

from incidentops.verification.verifier import VerifySpec, evaluate_lag, load_verify_config

SPEC = VerifySpec(
    lag_max=100,
    observation_window_seconds=60,
    sample_min_count=5,
    recovery_timeout_seconds=120,
    poll_interval_seconds=5,
)


@pytest.mark.parametrize(
    ("points", "passed"),
    [
        ([2000, 900, 80, 10, 0, 0, 0], True),
        ([0, 0, 0, 0, 0], True),
        ([2000, 1500, 1200, 1100, 1000], False),  # draining but not within bound
        ([0, 0, 50, 90, 99], False),  # within bound but rising: producer outpaces consumer
        ([10, 10, 10, 150, 10], False),  # one spike inside the sustain window
    ],
)
def test_lag_rule(points: list[float], passed: bool) -> None:
    assert evaluate_lag(points, SPEC).passed is passed


def test_too_few_samples_is_unknown() -> None:
    check = evaluate_lag([0, 0], SPEC)
    assert check.passed is None
    assert "only 2" in check.observed


def test_repository_config_has_resume_spec() -> None:
    assert load_verify_config().specs["resume_test_consumer"].lag_max == 100


def test_target_specific_specs_take_precedence() -> None:
    config = load_verify_config()
    redis = config.spec_for("restart_lab_dependency", "lab-redis")
    kafka = config.spec_for("restart_lab_dependency", "lab-kafka")
    postgres = config.spec_for("restart_lab_dependency", "lab-postgres")
    assert redis is not None
    assert kafka is not None
    assert postgres is not None
    assert {c.check_id for c in kafka.checks} >= {"broker_visible", "producer_sending"}
    assert "postgres_up" in {c.check_id for c in postgres.checks}
    assert "redis_up" in {c.check_id for c in redis.checks}
    # No generic restart spec: an unknown restart target verifies UNKNOWN.
    assert config.spec_for("restart_lab_dependency", "lab-api") is None
    assert config.spec_for("resume_test_consumer", "persist-demo-consumer") is not None


def test_every_catalog_action_target_has_a_verification_spec() -> None:
    from incidentops.domain.catalog import load_catalog

    config = load_verify_config()
    missing = [
        (action, service.service_id)
        for service in load_catalog().services
        for action in service.allowed_actions
        if config.spec_for(action, service.service_id) is None
    ]
    assert missing == []
