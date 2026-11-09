# ADR-010: Controller operation ledger and generation pinning

- **Status:** Accepted (Phase 0 default)
- **Resolves:** architecture.md §17 question 3

## Context

The executor must reconcile ambiguous outcomes by `action_id` and approvals
must be bound to a target revision.

## Decision

- Each controllable lab target has a monotonically increasing integer
  **generation**, rendered as `gen-<n>`. Every accepted state change (pause,
  resume, reset) increments it.
- A mutating request carries `action_id` and `expected_generation`. A stale
  generation is rejected with HTTP 409 and does not mutate state.
- Every accepted operation is stored in an operation ledger keyed by
  `action_id`. Repeating a request with the same `action_id` and payload returns
  the stored receipt without a second state change; the same `action_id` with a
  different payload is rejected with 409.
- The worker persists the ledger in a SQLite file on its own volume, so receipts
  survive a worker restart. Lab PostgreSQL is not used because it is itself a
  fault-injection target.
- The future deployment controller (rollback) follows the same contract, with
  the generation replaced by the pinned image digest of the registered service.

## Consequences

Reconciliation is a read of `GET /control/operations/{action_id}` plus target
status; it never re-sends a mutation blindly.
