"""Security invariants for role identities (P0 gate: agent cannot inject faults)."""

from __future__ import annotations

import pytest

from incidentops.security.identities import (
    EXTERNAL_MUTATIONS,
    ROLE_CAPABILITIES,
    AuthorizationError,
    Capability,
    IdentityRegistry,
    Role,
    has_capability,
    require_capability,
)

TOKENS = {role: f"{role.value}-token-0123456789" for role in Role}


@pytest.mark.parametrize("capability", sorted(EXTERNAL_MUTATIONS))
def test_agent_holds_no_external_mutation(capability: Capability) -> None:
    assert not has_capability(Role.AGENT, capability)
    with pytest.raises(AuthorizationError):
        require_capability(Role.AGENT, capability)


@pytest.mark.parametrize("capability", sorted(EXTERNAL_MUTATIONS))
def test_each_external_mutation_has_exactly_one_owner(capability: Capability) -> None:
    owners = [role for role, caps in ROLE_CAPABILITIES.items() if capability in caps]
    assert len(owners) == 1


def test_only_executor_dispatches_and_only_injector_injects() -> None:
    assert [r for r in Role if has_capability(r, Capability.DISPATCH_REMEDIATION)] == [
        Role.EXECUTOR
    ]
    assert [r for r in Role if has_capability(r, Capability.INJECT_FAULT)] == [Role.FAULT_INJECTOR]


def test_executor_cannot_inject_and_injector_cannot_remediate() -> None:
    assert not has_capability(Role.EXECUTOR, Capability.INJECT_FAULT)
    assert not has_capability(Role.FAULT_INJECTOR, Capability.DISPATCH_REMEDIATION)


def test_verifier_is_independent_of_executor() -> None:
    assert not has_capability(Role.VERIFIER, Capability.DISPATCH_REMEDIATION)
    assert not has_capability(Role.EXECUTOR, Capability.WRITE_VERIFICATION)


def test_every_role_has_capabilities() -> None:
    assert set(ROLE_CAPABILITIES) == set(Role)


def test_registry_authenticates_each_role() -> None:
    registry = IdentityRegistry.from_tokens(TOKENS)
    for role, token in TOKENS.items():
        assert registry.authenticate(token) is role


@pytest.mark.parametrize("token", [None, "", "wrong-token-0123456789"])
def test_registry_rejects_unknown_tokens(token: str | None) -> None:
    assert IdentityRegistry.from_tokens(TOKENS).authenticate(token) is None


def test_registry_rejects_shared_tokens() -> None:
    shared = "same-token-0123456789"
    with pytest.raises(ValueError, match="share a token"):
        IdentityRegistry.from_tokens({Role.AGENT: shared, Role.EXECUTOR: shared})


def test_registry_rejects_short_tokens() -> None:
    with pytest.raises(ValueError, match="too short"):
        IdentityRegistry.from_tokens({Role.AGENT: "short"})


def test_registry_does_not_retain_plaintext() -> None:
    registry = IdentityRegistry.from_tokens(TOKENS)
    assert TOKENS[Role.EXECUTOR] not in repr(registry)
