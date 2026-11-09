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
