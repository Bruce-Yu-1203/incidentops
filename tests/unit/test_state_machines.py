"""Transition tests for the four lifecycle state machines.

The expected edge sets below are written out independently of the
implementation so that any accidental change to a machine fails loudly.
"""

from __future__ import annotations

import itertools
from collections import deque
from enum import StrEnum

import pytest

from incidentops.domain.state_machines import (
    ACTION_MACHINE,
    APPROVAL_MACHINE,
    INCIDENT_MACHINE,
    RUN_MACHINE,
    ActionState,
    ApprovalState,
    IllegalTransitionError,
    IncidentState,
    RunState,
    StateMachine,
)

I, R, A, P = IncidentState, RunState, ActionState, ApprovalState  # noqa: E741

EXPECTED_INCIDENT_EDGES = {
    (I.RECEIVED, I.TRIAGED),
    (I.TRIAGED, I.INVESTIGATING),
    (I.INVESTIGATING, I.DIAGNOSED),
    (I.DIAGNOSED, I.DIAGNOSED_ONLY),
    (I.DIAGNOSED, I.INSUFFICIENT_EVIDENCE),
    (I.DIAGNOSED, I.AWAITING_APPROVAL),
    (I.INSUFFICIENT_EVIDENCE, I.ESCALATED),
    (I.AWAITING_APPROVAL, I.EXECUTING),
    (I.AWAITING_APPROVAL, I.ESCALATED),
    (I.EXECUTING, I.VERIFYING),
    (I.EXECUTING, I.ESCALATED),
    (I.VERIFYING, I.RESOLVED),
    (I.VERIFYING, I.ESCALATED),
    *(
        (s, I.FAILED)
        for s in (
            I.RECEIVED,
            I.TRIAGED,
            I.INVESTIGATING,
            I.DIAGNOSED,
            I.INSUFFICIENT_EVIDENCE,
            I.AWAITING_APPROVAL,
            I.EXECUTING,
            I.VERIFYING,
        )
    ),
    *(
        (s, I.CANCELLED)
        for s in (
            I.RECEIVED,
            I.TRIAGED,
            I.INVESTIGATING,
            I.DIAGNOSED,
            I.INSUFFICIENT_EVIDENCE,
            I.AWAITING_APPROVAL,
        )
    ),
}

EXPECTED_RUN_EDGES = {
    (R.QUEUED, R.RUNNING),
    (R.QUEUED, R.CANCELLED),
    (R.QUEUED, R.FAILED),
    (R.RUNNING, R.COMPLETED),
    (R.RUNNING, R.FAILED),
    (R.RUNNING, R.CANCELLED),
    (R.RUNNING, R.BUDGET_EXCEEDED),
}

EXPECTED_ACTION_EDGES = {
    (A.PROPOSED, A.POLICY_DENIED),
    (A.PROPOSED, A.PENDING_APPROVAL),
    (A.PENDING_APPROVAL, A.REJECTED),
    (A.PENDING_APPROVAL, A.EXPIRED),
    (A.PENDING_APPROVAL, A.APPROVED),
    (A.PENDING_APPROVAL, A.CANCELLED),
    (A.APPROVED, A.QUEUED),
    (A.APPROVED, A.EXPIRED),
    (A.APPROVED, A.CANCELLED),
    (A.QUEUED, A.EXECUTING),
    (A.QUEUED, A.EXPIRED),
    (A.QUEUED, A.CANCELLED),
    (A.QUEUED, A.FAILED),
    (A.EXECUTING, A.SUCCEEDED),
    (A.EXECUTING, A.FAILED),
    (A.EXECUTING, A.RECONCILING),
    (A.RECONCILING, A.SUCCEEDED),
    (A.RECONCILING, A.FAILED),
    (A.RECONCILING, A.ESCALATED),
}

EXPECTED_APPROVAL_EDGES = {
    (P.PENDING, P.APPROVED),
    (P.PENDING, P.REJECTED),
    (P.PENDING, P.EXPIRED),
    (P.PENDING, P.REVOKED),
    (P.APPROVED, P.REVOKED),
}

CASES: list[tuple[StateMachine[StrEnum], set[tuple[StrEnum, StrEnum]]]] = [
    (INCIDENT_MACHINE, EXPECTED_INCIDENT_EDGES),  # type: ignore[list-item]
    (RUN_MACHINE, EXPECTED_RUN_EDGES),  # type: ignore[list-item]
    (ACTION_MACHINE, EXPECTED_ACTION_EDGES),  # type: ignore[list-item]
    (APPROVAL_MACHINE, EXPECTED_APPROVAL_EDGES),  # type: ignore[list-item]
]


def _all_pairs(
    machine: StateMachine[StrEnum],
) -> list[tuple[StrEnum, StrEnum]]:
    states = sorted(machine.states)
    return [(a, b) for a in states for b in states]


@pytest.mark.parametrize(("machine", "expected"), CASES, ids=lambda c: getattr(c, "name", ""))
def test_edge_table_matches_specification(
    machine: StateMachine[StrEnum], expected: set[tuple[StrEnum, StrEnum]]
) -> None:
    for source, target in _all_pairs(machine):
        assert machine.can_transition(source, target) == ((source, target) in expected), (
            f"{machine.name}: {source} -> {target}"
        )


@pytest.mark.parametrize(("machine", "expected"), CASES, ids=lambda c: getattr(c, "name", ""))
def test_illegal_transitions_raise(
    machine: StateMachine[StrEnum], expected: set[tuple[StrEnum, StrEnum]]
) -> None:
    for source, target in _all_pairs(machine):
        if (source, target) in expected:
            machine.ensure_transition(source, target)
        else:
            with pytest.raises(IllegalTransitionError) as info:
                machine.ensure_transition(source, target)
            assert info.value.machine == machine.name


@pytest.mark.parametrize(("machine", "expected"), CASES, ids=lambda c: getattr(c, "name", ""))
def test_every_state_reachable_from_initial(
    machine: StateMachine[StrEnum], expected: set[tuple[StrEnum, StrEnum]]
) -> None:
    seen = {machine.initial}
    queue = deque([machine.initial])
    while queue:
        current = queue.popleft()
        for nxt in machine.allowed_targets(current):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    assert seen == machine.states


@pytest.mark.parametrize(("machine", "expected"), CASES, ids=lambda c: getattr(c, "name", ""))
def test_terminal_states_have_no_outgoing_edges(
    machine: StateMachine[StrEnum], expected: set[tuple[StrEnum, StrEnum]]
) -> None:
    for state in machine.terminal_states:
        assert not any(src == state for src, _ in expected)
        assert machine.is_terminal(state)


def test_incident_terminal_states() -> None:
    assert INCIDENT_MACHINE.terminal_states == {
        I.DIAGNOSED_ONLY,
        I.RESOLVED,
        I.ESCALATED,
        I.FAILED,
        I.CANCELLED,
    }


def test_incident_cannot_resolve_without_verification() -> None:
    # RESOLVED is reachable only from VERIFYING (independent verifier).
    sources = {s for s, t in EXPECTED_INCIDENT_EDGES if t is I.RESOLVED}
    assert sources == {I.VERIFYING}


def test_incident_happy_path_kafka_slice() -> None:
    path = [
        I.RECEIVED,
        I.TRIAGED,
        I.INVESTIGATING,
        I.DIAGNOSED,
        I.AWAITING_APPROVAL,
        I.EXECUTING,
        I.VERIFYING,
        I.RESOLVED,
    ]
    for source, target in itertools.pairwise(path):
        INCIDENT_MACHINE.ensure_transition(source, target)


def test_action_cannot_execute_without_approval() -> None:
    # Every path into EXECUTING must pass through APPROVED and QUEUED.
    assert {s for s, t in EXPECTED_ACTION_EDGES if t is A.EXECUTING} == {A.QUEUED}
    assert {s for s, t in EXPECTED_ACTION_EDGES if t is A.QUEUED} == {A.APPROVED}
    assert {s for s, t in EXPECTED_ACTION_EDGES if t is A.APPROVED} == {A.PENDING_APPROVAL}


def test_action_cannot_be_cancelled_once_executing() -> None:
    for state in (A.EXECUTING, A.RECONCILING, A.SUCCEEDED):
        assert not ACTION_MACHINE.can_transition(state, A.CANCELLED)


def test_reconciling_never_returns_to_executing() -> None:
    # No blind replay of an ambiguous external write (NFR-010).
    assert not ACTION_MACHINE.can_transition(A.RECONCILING, A.EXECUTING)
    assert not ACTION_MACHINE.can_transition(A.RECONCILING, A.QUEUED)


def test_error_message_names_machine_and_states() -> None:
    with pytest.raises(IllegalTransitionError, match=r"incident: illegal transition RESOLVED"):
        INCIDENT_MACHINE.ensure_transition(I.RESOLVED, I.INVESTIGATING)


def test_machine_rejects_unknown_states_in_edges() -> None:
    bad_edges: dict[StrEnum, frozenset[StrEnum]] = {A.PROPOSED: frozenset()}
    with pytest.raises(ValueError, match="unknown states"):
        StateMachine[StrEnum](name="bad", initial=R.QUEUED, edges=bad_edges)
