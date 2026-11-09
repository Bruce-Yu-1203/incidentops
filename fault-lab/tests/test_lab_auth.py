"""Tests for Fault Lab token roles."""

from __future__ import annotations

import pytest
from conftest import TOKENS
from fault_lab.auth import TOKEN_ENV, LabAuthenticator, LabRole


def test_each_token_maps_to_its_role(authenticator: LabAuthenticator) -> None:
    for role, token in TOKENS.items():
        assert authenticator.authenticate(f"Bearer {token}") is role


@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer wrong-token-000000"])
def test_rejects_bad_headers(authenticator: LabAuthenticator, header: str | None) -> None:
    assert authenticator.authenticate(header) is None


def test_shared_tokens_rejected() -> None:
    with pytest.raises(ValueError, match="share"):
        LabAuthenticator({LabRole.BENCHMARK: "x" * 20, LabRole.EXECUTOR: "x" * 20})


def test_from_env_requires_all_tokens() -> None:
    with pytest.raises(RuntimeError, match="missing lab tokens"):
        LabAuthenticator.from_env({TOKEN_ENV[LabRole.BENCHMARK]: "b" * 20})


def test_from_env_builds_authenticator() -> None:
    env = {TOKEN_ENV[role]: token for role, token in TOKENS.items()}
    auth = LabAuthenticator.from_env(env)
    assert auth.authenticate(f"Bearer {TOKENS[LabRole.READER]}") is LabRole.READER
