"""Human disposition of incidents that automation handed over (ADR-021).

Automation ends some incidents in a terminal state that still needs a person:
ESCALATED, FAILED (IncidentOps itself failed) and DIAGNOSED_ONLY (a diagnosis
without an executable action). The lifecycle state stays as automation left it;
a person records ownership and a closing disposition on top of it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from incidentops.domain.state_machines import IncidentState


class Disposition(StrEnum):
    FIXED_MANUALLY = "FIXED_MANUALLY"
    RECOVERED_ON_ITS_OWN = "RECOVERED_ON_ITS_OWN"
    FALSE_ALARM = "FALSE_ALARM"
    WONT_FIX = "WONT_FIX"


# Terminal states where automation stopped without a verified recovery.
HANDOVER_STATES: Final = frozenset(
    {IncidentState.ESCALATED, IncidentState.FAILED, IncidentState.DIAGNOSED_ONLY}
)


def needs_human(state: IncidentState, disposition: Disposition | None) -> bool:
    """True while a handed-over incident has no closing disposition."""
    return state in HANDOVER_STATES and disposition is None
