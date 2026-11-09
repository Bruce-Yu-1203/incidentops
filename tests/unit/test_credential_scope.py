"""Each process refuses credentials of other roles (NFR-011)."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from incidentops.api.app import create_app
from incidentops.config import Settings
from incidentops.security.identities import Role
from incidentops.workers.credentials import CredentialScopeError, assert_credential_scope


def _settings(**values: str) -> Settings:
    return Settings(_env_file=None, **{k: SecretStr(v) for k, v in values.items()})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("role", "field"),
    [
        (Role.AGENT, "executor_lab_token"),
        (Role.EXECUTOR, "anthropic_api_key"),
        (Role.EXECUTOR, "lab_read_token"),
        (Role.VERIFIER, "executor_lab_token"),
        (Role.API, "executor_lab_token"),
        (Role.API, "anthropic_api_key"),
    ],
)
def test_foreign_credentials_are_refused(role: Role, field: str) -> None:
    with pytest.raises(CredentialScopeError, match=field):
        assert_credential_scope(role, _settings(**{field: "x" * 20}))


@pytest.mark.parametrize(
    ("role", "field"),
    [
        (Role.AGENT, "lab_read_token"),
        (Role.AGENT, "anthropic_api_key"),
        (Role.EXECUTOR, "executor_lab_token"),
        (Role.VERIFIER, "lab_read_token"),
    ],
)
def test_own_credentials_are_accepted(role: Role, field: str) -> None:
    assert_credential_scope(role, _settings(**{field: "x" * 20}))


def test_api_will_not_start_with_executor_token() -> None:
    with pytest.raises(CredentialScopeError):
        create_app(_settings(executor_lab_token="x" * 20))
