"""Model adapter selection and endpoint configuration (ADR-008, ADR-019)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from incidentops.agent.baseline import RulesBaselineAdapter
from incidentops.agent.model import AnthropicModelAdapter
from incidentops.config import Settings
from incidentops.workers.investigation import budgets_from, build_model


def _settings(**values: object) -> Settings:
    return Settings(**values)  # type: ignore[arg-type]


def test_default_endpoint_is_anthropic() -> None:
    model = build_model(_settings(model_id="m", anthropic_api_key=SecretStr("k" * 20)))
    assert isinstance(model, AnthropicModelAdapter)
    assert model.base_url.startswith("https://api.anthropic.com")


def test_compatible_endpoint_is_used() -> None:
    model = build_model(
        _settings(
            model_id="deepseek-chat",
            anthropic_api_key=SecretStr("k" * 20),
            anthropic_base_url="https://api.deepseek.com/anthropic/",
        )
    )
    assert isinstance(model, AnthropicModelAdapter)
    assert model.base_url.rstrip("/") == "https://api.deepseek.com/anthropic"
    assert model.model_id == "deepseek-chat"


@pytest.mark.parametrize("url", ["http://api.example.com", "ftp://x", "api.deepseek.com"])
def test_non_https_endpoints_are_rejected(url: str) -> None:
    # The API key travels to this URL, so it must not go over plain HTTP.
    with pytest.raises(ValidationError):
        _settings(anthropic_base_url=url)


def test_loopback_http_and_empty_values_are_allowed() -> None:
    assert _settings(anthropic_base_url="http://127.0.0.1:4000").anthropic_base_url == (
        "http://127.0.0.1:4000"
    )
    assert _settings(anthropic_base_url="").anthropic_base_url is None


def test_rules_baseline_needs_no_key() -> None:
    assert isinstance(build_model(_settings(model_id="rules-baseline-v1")), RulesBaselineAdapter)


def test_missing_key_stops_the_worker() -> None:
    with pytest.raises(SystemExit):
        build_model(_settings(model_id="deepseek-chat"))


def test_output_cap_defaults_to_2048() -> None:
    model = build_model(_settings(model_id="m", anthropic_api_key=SecretStr("k" * 20)))
    assert isinstance(model, AnthropicModelAdapter)
    assert model.max_output_tokens == 2048


def test_output_cap_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    # Thinking models spend part of the cap on thinking, so it must be raisable.
    monkeypatch.setenv("INCIDENTOPS_MODEL_MAX_OUTPUT_TOKENS", "8192")
    model = build_model(Settings(model_id="m", anthropic_api_key=SecretStr("k" * 20)))
    assert isinstance(model, AnthropicModelAdapter)
    assert model.max_output_tokens == 8192


@pytest.mark.parametrize("cap", [0, 100, 100_000])
def test_out_of_range_output_caps_are_rejected(cap: int) -> None:
    with pytest.raises(ValidationError):
        _settings(model_id="m", model_max_output_tokens=cap)


def test_llm_runs_get_three_repair_attempts_by_default() -> None:
    budgets = budgets_from(_settings(model_id="deepseek-flash"))
    assert budgets.max_repair_attempts == 3


def test_rules_baseline_keeps_one_repair_attempt() -> None:
    budgets = budgets_from(_settings(model_id="rules-baseline-v1", max_repair_attempts=5))
    assert budgets.max_repair_attempts == 1


def test_repair_attempts_are_configurable_for_llm_runs() -> None:
    assert budgets_from(_settings(model_id="m", max_repair_attempts=5)).max_repair_attempts == 5
