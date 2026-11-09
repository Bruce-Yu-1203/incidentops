"""Tests for runtime configuration validation."""

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
