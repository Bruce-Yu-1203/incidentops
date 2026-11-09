"""Bearer-token roles for Fault Lab management endpoints.

Three separate tokens exist so that the benchmark controller (fault injection),
the IncidentOps executor (remediation) and read-only observers (agent tools,
verifier) can never borrow each other's privileges.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Mapping
from enum import StrEnum


class LabRole(StrEnum):
    BENCHMARK = "benchmark"
    EXECUTOR = "executor"
    READER = "reader"


TOKEN_ENV = {
    LabRole.BENCHMARK: "FAULT_LAB_BENCHMARK_TOKEN",
    LabRole.EXECUTOR: "FAULT_LAB_EXECUTOR_TOKEN",
    LabRole.READER: "FAULT_LAB_READ_TOKEN",
}


class LabAuthenticator:
    """Resolve a bearer token to exactly one lab role."""

    def __init__(self, tokens: Mapping[LabRole, str]) -> None:
        digests: dict[bytes, LabRole] = {}
        for role, token in tokens.items():
            if len(token) < 16:
                raise ValueError(f"{TOKEN_ENV[role]} must be at least 16 characters")
            digest = hashlib.sha256(token.encode()).digest()
            if digest in digests:
                raise ValueError("lab roles must not share tokens")
            digests[digest] = role
        self._digests = digests

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> LabAuthenticator:
        missing = [name for name in TOKEN_ENV.values() if not environ.get(name)]
        if missing:
            raise RuntimeError(f"missing lab tokens: {', '.join(missing)}")
        return cls({role: environ[name] for role, name in TOKEN_ENV.items()})

    def authenticate(self, authorization: str | None) -> LabRole | None:
        if not authorization or not authorization.startswith("Bearer "):
            return None
        candidate = hashlib.sha256(authorization.removeprefix("Bearer ").encode()).digest()
        matched: LabRole | None = None
        for digest, role in self._digests.items():
            if hmac.compare_digest(digest, candidate):
                matched = role
        return matched
