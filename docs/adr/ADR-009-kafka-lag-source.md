# ADR-009: Kafka lag source and consumer control contract

- **Status:** Accepted (Phase 0 default)
- **Resolves:** architecture.md §17 question 2

## Context

Kafka has no broker-side "pause consumer group" API, and lag metric names differ
between exporters.

## Decision

- Group lag is exported by `danielqsj/kafka-exporter` (metric family
  `kafka_consumergroup_lag`), pinned by image tag. Query templates reference a
  `template_id`, never a raw metric name, and are contract-tested in P1.
- The controllable worker additionally exports its own processed-message
  counter and `paused` gauge, so a paused consumer is distinguishable from a
  broker outage.
- Pause/resume is implemented inside the lab worker by pausing its assigned
  partitions while it keeps polling, so the group does not rebalance.
- Control endpoints: `GET /control/status`, `POST /control/pause`
  (benchmark-controller token only), `POST /control/resume` (executor token
  only), `POST /control/reset` (benchmark-controller token only) and
  `GET /control/operations/{action_id}`.

## Consequences

If a different lag exporter is adopted, only the query template and its
contract test change.
