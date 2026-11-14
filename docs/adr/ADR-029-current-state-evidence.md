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

### 3c. Range summaries lead with the current value and date the peak

Every range reading's summary (metrics, consumer lag) now reads, for
example, "now last=0 locks; peak max=1 at 21:02:00Z, 12 min before the
latest sample; first=0". The snapshot also stores `peak_at` and `latest_at`
(the most recent sample at the peak value, and the latest sample). An old
peak is visibly old, and a current one says "(the latest sample)". The
`first=`, `last=` and `max=` tokens are kept, so the rules baseline reads
the same values.

### 3b. History reads start shortly before the incident

Windowed read tools (metrics, logs, traces, consumer lag) never look back
further than `INCIDENTOPS_HISTORY_PRE_WINDOW_MINUTES` (default **5**) before
the incident was opened. The tool gateway shortens a longer requested
`lookback_minutes` to that limit (at least 1 minute) and says so in the
reading ("window limited to the last N min ..."). The stored evidence records
the look-back actually used. Setting the value to 0 disables the limit.

Why 5 minutes: lab alerts fire within about two minutes of a fault (the
longest time to detection measured in the 2026-11-13/14 runs was about 80
s), so the onset stays visible. A longer window reaches back into earlier,
unrelated incidents, which is what misled the agent in both runs.
Deployment and configuration history are not windowed and are unaffected.
The chat agent is not limited either: it answers questions about an
incident at any later time.

In the benchmark, the runner keeps the lab healthy for at least
`clean_history_s` (default 330 s = 5 min + 30 s margin) before each injection,
even when a case's `baseline_seconds` is shorter. A 5-minute window can then
never contain the previous trial's fault. This lengthens each trial by about
4.5 minutes (a 14x5 run takes about five hours longer).
`--clean-history` / `BENCHMARK_CLEAN_HISTORY_S` overrides it; it must stay at
or above the pre-window.

## Consequences

- Policy `p7`, prompt `investigation-v11`. Grading and ground truth are
  unchanged.
- No-fault and back-to-back trials no longer see earlier faults. The agent
  can no longer see slow trends that started more than 5 minutes before an
  incident; raise the pre-window if that matters more than isolation.
