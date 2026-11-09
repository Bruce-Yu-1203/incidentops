"""Model adapter selection and endpoint configuration (ADR-008, ADR-019)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from incidentops.agent.baseline import RulesBaselineAdapter
from incidentops.agent.model import AnthropicModelAdapter
from incidentops.config import Settings
from incidentops.workers.investigation import build_model


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
