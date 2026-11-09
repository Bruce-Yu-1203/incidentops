"""Startup guard: each process refuses credentials that belong to other roles.

Local development loads one ``.env``, so separation is enforced at process
start: the make targets strip foreign variables, and this guard fails the
process if any remain (architecture §15, NFR-011).
"""

from __future__ import annotations

from typing import Final

from incidentops.config import Settings
from incidentops.security.identities import Role

# Settings fields that must be absent for each process role.
FORBIDDEN: Final[dict[Role, frozenset[str]]] = {
    Role.AGENT: frozenset({"executor_lab_token"}),
    Role.EXECUTOR: frozenset({"anthropic_api_key", "lab_read_token"}),
    Role.VERIFIER: frozenset({"executor_lab_token", "anthropic_api_key"}),
    Role.API: frozenset({"executor_lab_token", "anthropic_api_key"}),
}


class CredentialScopeError(RuntimeError):
    """A process was started with another role's credentials."""


def assert_credential_scope(role: Role, settings: Settings) -> None:
    leaked = sorted(name for name in FORBIDDEN.get(role, frozenset()) if getattr(settings, name))
    if leaked:
        raise CredentialScopeError(
            f"{role.value} process must not hold: {', '.join(leaked)} "
            "(start it through the make target, which strips foreign credentials)"
        )
