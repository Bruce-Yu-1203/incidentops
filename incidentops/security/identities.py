"""Separate role identities for every control-plane process.

Each process authenticates with its own bearer token and receives only the
capabilities of its role. The agent role never holds a mutating capability;
that invariant is enforced here and by tests in ``tests/security``.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class Role(StrEnum):
    API = "api"
    ALERTMANAGER = "alertmanager"
    AGENT = "agent"
    EXECUTOR = "executor"
    VERIFIER = "verifier"
    FAULT_INJECTOR = "fault_injector"


class Capability(StrEnum):
    INGEST_ALERTS = "ingest_alerts"
    MANAGE_WORKFLOW = "manage_workflow"
    READ_TELEMETRY = "read_telemetry"
    READ_TARGET_STATUS = "read_target_status"
    WRITE_EVIDENCE = "write_evidence"
    PROPOSE_ACTION = "propose_action"
    DISPATCH_REMEDIATION = "dispatch_remediation"
    WRITE_VERIFICATION = "write_verification"
    INJECT_FAULT = "inject_fault"
    RESET_LAB = "reset_lab"


# Capabilities that change an external system. Each one must belong to exactly
# one role so a single compromised identity cannot both inject and remediate.
EXTERNAL_MUTATIONS: Final = frozenset(
    {Capability.DISPATCH_REMEDIATION, Capability.INJECT_FAULT, Capability.RESET_LAB}
)

ROLE_CAPABILITIES: Final[Mapping[Role, frozenset[Capability]]] = MappingProxyType(
    {
        Role.API: frozenset({Capability.MANAGE_WORKFLOW}),
        Role.ALERTMANAGER: frozenset({Capability.INGEST_ALERTS}),
        Role.AGENT: frozenset(
            {
                Capability.READ_TELEMETRY,
                Capability.READ_TARGET_STATUS,
                Capability.WRITE_EVIDENCE,
                Capability.PROPOSE_ACTION,
            }
        ),
        Role.EXECUTOR: frozenset({Capability.DISPATCH_REMEDIATION, Capability.READ_TARGET_STATUS}),
        Role.VERIFIER: frozenset(
            {
                Capability.READ_TELEMETRY,
                Capability.READ_TARGET_STATUS,
                Capability.WRITE_VERIFICATION,
            }
        ),
        Role.FAULT_INJECTOR: frozenset({Capability.INJECT_FAULT, Capability.RESET_LAB}),
    }
)


class AuthorizationError(PermissionError):
    """Raised when an identity lacks a required capability."""


def has_capability(role: Role, capability: Capability) -> bool:
    return capability in ROLE_CAPABILITIES[role]


def require_capability(role: Role, capability: Capability) -> None:
    if not has_capability(role, capability):
        raise AuthorizationError(f"role {role.value} lacks capability {capability.value}")


def _token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


@dataclass(frozen=True)
class IdentityRegistry:
    """Maps bearer tokens to roles without retaining plaintext tokens."""

    _digests: Mapping[bytes, Role]

    @classmethod
    def from_tokens(cls, tokens: Mapping[Role, str]) -> IdentityRegistry:
        digests: dict[bytes, Role] = {}
        for role, token in tokens.items():
            if len(token) < 16:
                raise ValueError(f"token for {role.value} is too short (min 16 chars)")
            digest = _token_digest(token)
            if digest in digests:
                # Shared tokens would collapse two identities into one.
                raise ValueError(f"roles {digests[digest].value} and {role.value} share a token")
            digests[digest] = role
        return cls(MappingProxyType(digests))

    def authenticate(self, token: str | None) -> Role | None:
        """Return the role for ``token`` or ``None``; compares in constant time."""
        if not token:
            return None
        candidate = _token_digest(token)
        matched: Role | None = None
        for digest, role in self._digests.items():
            if hmac.compare_digest(digest, candidate):
                matched = role
        return matched
