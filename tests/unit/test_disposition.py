"""Human disposition rules for handed-over incidents (ADR-021)."""

from __future__ import annotations

import pytest

from incidentops.domain.disposition import HANDOVER_STATES, Disposition, needs_human
from incidentops.domain.state_machines import INCIDENT_MACHINE, IncidentState


def test_handover_states_are_terminal_without_verified_recovery() -> None:
    assert INCIDENT_MACHINE.terminal_states >= HANDOVER_STATES
    assert IncidentState.RESOLVED not in HANDOVER_STATES
    assert IncidentState.CANCELLED not in HANDOVER_STATES


@pytest.mark.parametrize("state", sorted(HANDOVER_STATES))
def test_handover_needs_a_human_until_disposed(state: IncidentState) -> None:
    assert needs_human(state, None)
    assert not needs_human(state, Disposition.FIXED_MANUALLY)


@pytest.mark.parametrize(
    "state", [IncidentState.RESOLVED, IncidentState.CANCELLED, IncidentState.INVESTIGATING]
)
def test_other_states_never_need_disposition(state: IncidentState) -> None:
    assert not needs_human(state, None)
