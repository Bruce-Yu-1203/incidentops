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
