# ADR-015: Action cancellation and pre-dispatch revalidation failures

- **Status:** Accepted
- **Resolves:** gap between the action state machine (architecture.md §5.3)
  and the cancel API (§12), S-03, S-04 and S-10 (benchmark.md §8)

## Context

The API exposes `POST /actions/{id}/cancel` and the safety suite requires that
revoked, cancelled or expired approvals are refused by the executor, but the
action state machine has no `CANCELLED` state and no edge for a failed
revalidation after `QUEUED`.

## Decision

- Add terminal `ActionState.CANCELLED`, reachable from `PENDING_APPROVAL`,
  `APPROVED` and `QUEUED` — never once `EXECUTING` has started.
- `EXPIRED` is also reachable from `APPROVED` and `QUEUED`, because the executor
  re-checks the TTL immediately before dispatch.
- `QUEUED → FAILED` records a failed pre-dispatch revalidation (stale target
  revision, hash mismatch, policy changed). No external call is made; the reason
  code is stored and the incident may escalate or receive a new proposal.
- Incident cancellation is not allowed from `EXECUTING` or `VERIFYING`; those
  states must complete or escalate.
- `ApprovalState.APPROVED → REVOKED` lets an approver withdraw a decision before
  dispatch; the executor treats a revoked approval as not authorized.

## Consequences

Every refusal path before an external call ends in a distinct, auditable
terminal state.
