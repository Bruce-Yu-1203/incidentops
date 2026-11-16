"""Tests for runtime configuration validation."""

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from incidentops.config import Profile, Settings


def test_default_profile_is_dev() -> None:
    settings = Settings(_env_file=None)
    assert settings.profile is Profile.DEV


def test_production_profile_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(profile="production", _env_file=None)


def test_non_postgres_database_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(
            control_database_url=SecretStr("sqlite:///tmp.db"),
            _env_file=None,
        )


def test_database_url_is_not_rendered_in_repr() -> None:
    settings = Settings(_env_file=None)
    assert "incidentops@" not in repr(settings)


def test_settings_ignore_dotenv_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Credential stripping only works if no process silently re-reads .env.
    (tmp_path / ".env").write_text("INCIDENTOPS_EXECUTOR_LAB_TOKEN=leaked-token-0123456789\n")
    monkeypatch.chdir(tmp_path)
    assert Settings().executor_lab_token is None


def test_grouping_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.group_window_seconds == 180
    # Upstream alerts normally arrive first; no settle wait (ADR-030, 2026-11-16).
    assert settings.group_settle_seconds == 0
