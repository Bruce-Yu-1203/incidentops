"""Typed runtime configuration for the IncidentOps control plane."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Profile(StrEnum):
    """Deployment profiles from docs/tech-stack.md section 11."""

    DEV = "dev"
    BENCHMARK = "benchmark"
    DEMO = "demo"
    STAGING_OPTIONAL = "staging-optional"


class Settings(BaseSettings):
    """Control-plane settings loaded from environment variables.

    Production is intentionally not a valid profile: the MVP has no
    production remediation support, so a misconfigured deployment fails at
    startup instead of running with unexpected privileges.
    """

    model_config = SettingsConfigDict(
        env_prefix="INCIDENTOPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    profile: Profile = Profile.DEV
    control_database_url: SecretStr = Field(
        default=SecretStr(
            "postgresql+psycopg://incidentops:incidentops@localhost:5433/incidentops_control"
        )
    )
    alertmanager_token: SecretStr | None = None

    # Investigation agent (read-only identity). The model ID has no default so
    # every run records an explicitly configured model (ADR-008).
    model_id: str | None = None
    anthropic_api_key: SecretStr | None = None
    prometheus_url: str = "http://127.0.0.1:9090"
    loki_url: str = "http://127.0.0.1:3100"
    tempo_url: str = "http://127.0.0.1:3200"
    # Public Grafana base URL used only to build "open source" links in the UI.
    grafana_url: str = "http://127.0.0.1:3000"
    # Read-only Fault Lab token used by get_consumer_status. Never a write token.
    lab_read_token: SecretStr | None = None
    # Server-side overrides of catalog management URLs (service_id -> URL), e.g.
    # when the worker runs on the host instead of the lab network.
    endpoint_overrides: dict[str, str] = Field(default_factory=dict)
    session_ttl_hours: int = Field(default=12, ge=1, le=72)
    require_separation_of_duties: bool = True
    worker_lease_seconds: int = Field(default=600, ge=30, le=3600)
    max_agent_steps: int = Field(default=20, ge=1, le=100)
    max_tool_calls: int = Field(default=40, ge=1, le=200)
    investigation_timeout_seconds: int = Field(default=300, ge=10, le=3600)

    @field_validator("control_database_url")
    @classmethod
    def _require_postgres(cls, value: SecretStr) -> SecretStr:
        # The workflow store relies on PostgreSQL semantics (SKIP LOCKED,
        # partial unique indexes), so other engines are rejected up front.
        if not value.get_secret_value().startswith("postgresql"):
            raise ValueError("control_database_url must be a PostgreSQL URL")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
