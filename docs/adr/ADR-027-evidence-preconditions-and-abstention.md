# ADR-027: Evidence preconditions for database releases, and no-fault abstention

- **Status:** Accepted
- **Relates to:** ADR-024, ADR-025, ADR-026; policy `p6`; prompt `investigation-v9`

## Context

The 2026-11-13 B1 run (deepseek-flash, 14 cases x 5,
`docs/benchmark-results/2026-11-13-B1-deepseek-flash-v2.1/`) executed four
database releases that had nothing to release:

| Trial | Diagnosis | Action executed | Result |
|---|---|---|---|
| B-F07 run04 (Kafka broker) | postgres_lock_contention | release_lab_db_blocker | FAILED, wrong mutation |
| B-F13 run03 (config error) | postgres_lock_contention | release_lab_db_blocker | FAILED, scenario-prohibited |
| B-N01 run03 (no fault) | postgres_lock_contention | release_lab_db_blocker | FAILED, scenario-prohibited |
| B-F13 run04 (config error) | connection_pool_exhausted | release_lab_idle_connections | FAILED, wrong mutation |

Policy checked the root cause/action pair (ADR-024) and the confidence
(ADR-025), but not whether the cited telemetry showed the condition the
action fixes. The model cited lock and connection readings whose 30-minute
window still held an earlier trial's peak: the window **maximum** showed a
lock, while the latest sample did not.

## Decision

### 1. Evidence preconditions (policy p6)

A policy rule may list `required_evidence`: facts that the diagnosis's
**supporting and action evidence** must show about the action's **target**
before the action may be proposed. Otherwise policy denies it with
`required_evidence_missing`.

Facts are derived deterministically from the stored evidence snapshots by
`incidentops/policy/evidence_facts.py`, never from the model's prose:

| Fact | Shown by (target = the action's target) |
|---|---|
| `postgres_exclusive_lock_present` | latest sample > 0 of `postgres_exclusive_locks` or of the `accessexclusivelock` series of `postgres_locks_by_mode`; or an ACCESS EXCLUSIVE lock in a `postgres_health` reading |
| `postgres_connections_near_limit` | latest sample >= 0.8 of `postgres_connection_utilization`; or `connection_share` >= 0.8 in a `postgres_health` reading |

- `release_lab_db_blocker` requires `postgres_exclusive_lock_present`.
- `release_lab_idle_connections` requires `postgres_connections_near_limit`.
  It is the direct analogue (B-F13 run04 executed it wrongly), and the same
  window problem applies.

Facts use the **latest** sample, not the window maximum, so a resolved
earlier peak never satisfies a precondition. Unparsable or truncated
snapshots, evidence about another service, and contradicting evidence yield
no fact (fail closed). The executor's pre-dispatch re-check passes no facts
and skips the precondition: the diagnosis is immutable and was evaluated
when the proposal was made.

Replaying the run's 18 release proposals against their stored evidence: the
four wrong executions above are denied, and all nine releases that succeeded
are still allowed. The rules baseline cites `postgres_health` and proposes the
idle-connection release only at >= 0.9 utilization, so it is unaffected.

### 2. No-fault abstention (prompt investigation-v9)

All five B-N01 (no fault) trials named a root cause, three of them with
high confidence. The prompt now says explicitly that `insufficient_evidence`
is the expected answer when no causal signal is confirmed. That covers an
alert whose symptom has resolved or is within normal noise, and services,
changes and dependencies that all look healthy. A root cause and any proposed
action require a confirmed, current causal signal, and `high` confidence
requires explicit confirming evidence for the mechanism. No scenario data is
added to the prompt.

## Consequences

- Policy version `p6`; the reason code is documented in
  `docs/operations/failure-modes.md`.
- A correct lock or connection release now needs the model to cite a current
  lock or connection reading. That is the evidence a human approver needs
  anyway.
- The 0.8 threshold is a lab value. It is versioned with the code and should
  be revisited if the lab's `max_connections` or the hoarder change.
