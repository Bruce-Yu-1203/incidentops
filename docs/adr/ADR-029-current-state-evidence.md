# ADR-029: Root-cause component, restart precondition and current-state telemetry

- **Status:** Accepted
- **Relates to:** ADR-027, ADR-028; policy `p7`; prompt `investigation-v11`;
  `docs/proposals/2026-11-14-r2-follow-ups.md` (items 1, 2, 3b, 3c)

## Context

The 2026-11-14 r2 run (`docs/benchmark-results/2026-11-14-B1-deepseek-flash-v2.1-r2/`)
left nine non-PASS trials. Two named the alerting service instead of the
failing dependency (B-F12 r2, r4). One executed a database restart in the
no-fault case (B-N01 r1), based on readings from the previous trial's
outage, which was still inside the lookback window. The same window effect
caused the wrong releases of 2026-11-13 (ADR-027).

## Decision

### 1. `affected_component` is the root-cause component

The field description and a prompt rule say that `affected_component` is
the component where the root cause lives. When the alerting service fails
because one of its dependencies fails, the diagnosis names the dependency,
even for the incident opened on the alerting service. Every incident caused
by one fault gets the same `affected_component`. Grading is unchanged.
Prompt `investigation-v11`.

### 2. Restarts need the dependency down now (policy p7)

`restart_lab_dependency` gets the ADR-027 precondition style:
`required_evidence: [dependency_down_now]`. The fact is derived from the
cited supporting and action evidence about the target, using the **latest**
sample or an instant reading:

| Target | Shown by |
|---|---|
| PostgreSQL | `postgres_up` latest = 0, or `up=0` in a `postgres_health` reading |
| Redis | `redis_up` latest = 0, or `redis_up` = 0 in a `redis_health` reading |
| Kafka broker | `kafka_broker_count` latest = 0, or `producer_send_error_rate` latest > 0 on a producer of that broker |

The broker's own count is not enough for Kafka: during the lab's broker
outage the exporter still reported one broker. The lab has one broker, and
producers bind a topic rather than a broker, so every registered producer
counts as a producer of it. A catalog with several brokers needs a
producer-to-broker binding first. Without the fact, the proposal is denied
with `required_evidence_missing`.

**Replay** against the stored evidence of all 57 restart proposals in the
2026-11-13 and 2026-11-14 runs:

- **B-N01 r1 (2026-11-14), the restart executed in the no-fault case:**
  denied (`postgres_up` latest = 1; it had dipped earlier in the window).
- **28 restarts that succeeded legitimately:** 27 allowed. The exception is
  one Kafka restart (2026-11-13) that cited only consumer and API readings,
  with no current broker or producer signal; it would now be denied.
- **28 proposals denied at the time for other reasons** (duplicate active
  action): all would pass this precondition.
