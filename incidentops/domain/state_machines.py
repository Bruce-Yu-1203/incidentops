"""Explicit lifecycle state machines (docs/architecture.md section 5).

Incident, investigation run, action and approval each have their own state
machine. Mixing them into one status column would blur the difference between
"the agent finished", "the action succeeded" and "the incident is resolved".

The machines here are pure and side-effect free. Persistence code must call
``ensure_transition`` before issuing its compare-and-swap update so that an
illegal transition is rejected before any row is touched.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum


class IllegalTransitionError(ValueError):
    """Raised when a transition is not allowed by a state machine."""

    def __init__(self, machine: str, source: StrEnum, target: StrEnum) -> None:
        super().__init__(f"{machine}: illegal transition {source.value} -> {target.value}")
        self.machine = machine
        self.source = source
        self.target = target


class IncidentState(StrEnum):
    RECEIVED = "RECEIVED"
    TRIAGED = "TRIAGED"
    INVESTIGATING = "INVESTIGATING"
    DIAGNOSED = "DIAGNOSED"
    DIAGNOSED_ONLY = "DIAGNOSED_ONLY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RunState(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


class ActionState(StrEnum):
    PROPOSED = "PROPOSED"
    POLICY_DENIED = "POLICY_DENIED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    APPROVED = "APPROVED"
    QUEUED = "QUEUED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RECONCILING = "RECONCILING"
    ESCALATED = "ESCALATED"


class ApprovalState(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


@dataclass(frozen=True)
class StateMachine[S: StrEnum]:
    """A finite state machine described by an explicit edge table."""

    name: str
    initial: S
    edges: Mapping[S, frozenset[S]]
    states: frozenset[S] = field(init=False)

    def __post_init__(self) -> None:
        state_type = type(self.initial)
        object.__setattr__(self, "states", frozenset(state_type))
        unknown = set(self.edges) - self.states
        if unknown:
            raise ValueError(f"{self.name}: edges reference unknown states {unknown}")

    @property
    def terminal_states(self) -> frozenset[S]:
        """States with no outgoing transitions."""
        return frozenset(s for s in self.states if not self.edges.get(s))

    def is_terminal(self, state: S) -> bool:
        return not self.edges.get(state)

    def allowed_targets(self, state: S) -> frozenset[S]:
        return self.edges.get(state, frozenset())

    def can_transition(self, source: S, target: S) -> bool:
        return target in self.allowed_targets(source)

    def ensure_transition(self, source: S, target: S) -> None:
        """Raise ``IllegalTransitionError`` unless ``source -> target`` is allowed."""
        if not self.can_transition(source, target):
            raise IllegalTransitionError(self.name, source, target)


def _edges[S: StrEnum](table: Mapping[S, set[S]]) -> dict[S, frozenset[S]]:
    return {source: frozenset(targets) for source, targets in table.items()}


_I = IncidentState
# States from which an authorized operator may still cancel. EXECUTING and
# VERIFYING are excluded: an external operation may be in flight, so those
# states must finish or escalate rather than be cancelled.
_INCIDENT_CANCELLABLE = {
    _I.RECEIVED,
    _I.TRIAGED,
    _I.INVESTIGATING,
    _I.DIAGNOSED,
    _I.INSUFFICIENT_EVIDENCE,
    _I.AWAITING_APPROVAL,
}
_INCIDENT_TABLE: dict[IncidentState, set[IncidentState]] = {
    _I.RECEIVED: {_I.TRIAGED},
    _I.TRIAGED: {_I.INVESTIGATING},
    _I.INVESTIGATING: {_I.DIAGNOSED},
    _I.DIAGNOSED: {_I.DIAGNOSED_ONLY, _I.INSUFFICIENT_EVIDENCE, _I.AWAITING_APPROVAL},
    _I.INSUFFICIENT_EVIDENCE: {_I.ESCALATED},
    _I.AWAITING_APPROVAL: {_I.EXECUTING, _I.ESCALATED},
    _I.EXECUTING: {_I.VERIFYING, _I.ESCALATED},
    # Only a PASSED verification may resolve; VERIFICATION_FAILED and UNKNOWN
    # outcomes escalate (ADR-013).
    _I.VERIFYING: {_I.RESOLVED, _I.ESCALATED},
}
for _state in list(_INCIDENT_TABLE):
    # FAILED marks a platform/internal failure and is reachable from every
    # non-terminal state.
    _INCIDENT_TABLE[_state].add(_I.FAILED)
for _state in _INCIDENT_CANCELLABLE:
    _INCIDENT_TABLE[_state].add(_I.CANCELLED)

INCIDENT_MACHINE: StateMachine[IncidentState] = StateMachine(
    name="incident", initial=_I.RECEIVED, edges=_edges(_INCIDENT_TABLE)
)

_R = RunState
RUN_MACHINE: StateMachine[RunState] = StateMachine(
    name="investigation_run",
    initial=_R.QUEUED,
    edges=_edges(
        {
            _R.QUEUED: {_R.RUNNING, _R.CANCELLED, _R.FAILED},
            _R.RUNNING: {_R.COMPLETED, _R.FAILED, _R.CANCELLED, _R.BUDGET_EXCEEDED},
        }
    ),
)

_A = ActionState
ACTION_MACHINE: StateMachine[ActionState] = StateMachine(
    name="action",
    initial=_A.PROPOSED,
    edges=_edges(
        {
            _A.PROPOSED: {_A.POLICY_DENIED, _A.PENDING_APPROVAL},
            _A.PENDING_APPROVAL: {_A.REJECTED, _A.EXPIRED, _A.APPROVED, _A.CANCELLED},
            # Approved work can still be cancelled or expire before dispatch;
            # the executor rechecks immediately before the provider call (S-03, S-10).
            _A.APPROVED: {_A.QUEUED, _A.EXPIRED, _A.CANCELLED},
            # QUEUED -> FAILED covers a failed pre-dispatch revalidation such as a
            # stale target revision (S-04); no external call is made (ADR-015).
            _A.QUEUED: {_A.EXECUTING, _A.EXPIRED, _A.CANCELLED, _A.FAILED},
            _A.EXECUTING: {_A.SUCCEEDED, _A.FAILED, _A.RECONCILING},
            # Ambiguous outcomes never retry blindly: they resolve to a confirmed
            # result or escalate.
            _A.RECONCILING: {_A.SUCCEEDED, _A.FAILED, _A.ESCALATED},
        }
    ),
)

_P = ApprovalState
APPROVAL_MACHINE: StateMachine[ApprovalState] = StateMachine(
    name="approval",
    initial=_P.PENDING,
    edges=_edges(
        {
            _P.PENDING: {_P.APPROVED, _P.REJECTED, _P.EXPIRED, _P.REVOKED},
            _P.APPROVED: {_P.REVOKED},
        }
    ),
)
