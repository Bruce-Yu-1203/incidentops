# ADR-024: Root-cause/action consistency and expanded root-cause coverage

- **Status:** Accepted
- **Relates to:** architecture §9.1 (policy), benchmark.md §5, ADR-020

## Context

Policy checked an action's type, environment, target and arguments, but not
whether the action fits the diagnosis. A model could diagnose
`cpu_saturation` and still propose a perfectly allowlisted rollback, and the
proposal would reach a human approver. Several root causes in the taxonomy
also had no benchmark scenario, alert or remediation, and some tools had blind
spots (logs only for the HTTP API, no Redis health view).

## Decision

### Root cause -> action mapping (policy `p3`)

- `config/policy.yaml` gains a versioned `root_cause_actions` map from each
  taxonomy root cause to the only action types that may remediate it. The
  policy schema rejects a mapping that names an action without a rule.
- The proposal evaluator passes the diagnosis' `root_cause_id` to the policy
  engine. An action type that is not listed for that root cause is denied with
  reason `action_not_allowed_for_root_cause`; root causes with an empty list
  (`consumer_slow`, `application_error`, `insufficient_evidence`) or unknown
  root causes never get an approval request. Denials leave the incident in
  `DIAGNOSED_ONLY` for human review, as every policy denial does.
- The check runs at proposal time only. The executor's pre-dispatch re-check
  passes no root cause: the diagnosis is immutable and the envelope pins the
  policy version, so a changed mapping already blocks dispatch
  (`policy_version_changed`).
- The mapping restricts action *types*; targets are still restricted by the
  service catalog. The investigation prompt states the mapping
  (`investigation-v5`).

Later sections of this ADR record the remaining coverage decisions as they are
implemented.

### Memory leak evidence (B-F04)

- New template `api_memory_growth_rate` (`deriv(process_resident_memory_bytes[1m])`,
  bytes/s) and recording rule `lab_api:memory_growth_bytes_per_second:deriv1m`;
  the `lab_api_memory_growth` alert uses the recording rule with `for: 30s`
  (was a 2m deriv with `for: 1m`).
- The B0 baseline reads the growth rate and treats a new release whose peak
  growth rate in the lookback exceeds 500 KB/s as a leak, in addition to the
  existing level-growth rule. This covers the two failure modes in the
  2026-11-10 analysis: the leak cap flattening memory before investigation,
  and a lookback that still holds an earlier trial's peak. The leak cap stays
  in place to protect the lab host.

### Tool blind spots

- `search_logs` now applies to `kafka_consumer` and `kafka_producer` services.
  The consumer and producer export their logs over OTLP like the API, with the
  catalog `service_id` as `service.name`, so the same server-built LogQL
  (`{service_name="<service_id>"}`) finds them.
- New read-only tool `get_redis_health` (Redis services only): four instant
  Prometheus reads from the existing exporter (`redis_up`, memory, connected
  clients, command rate), each from a predeclared template. Same gateway
  limits as `get_postgres_health` (3 s timeout, 10 calls per run, 4 KiB).

### Scenarios for existing root causes (B-F07 to B-F10)

All injections follow ADR-020: no container control, bounded and reversible,
and the injected fault kind never reaches agent-visible telemetry.

| Case | Injection | Alert | Expected outcome | Look-alike kept apart by |
|---|---|---|---|---|
| B-F07 `kafka_broker_unavailable` | `lab-kafka-proxy` partitions the broker's client listener (open connections cut, new ones refused) for at most 420 s | `lab_kafka_broker_unreachable` (`kafka_brokers` < 1 for 15 s) | `restart_lab_dependency` on `lab-kafka`; `resume_test_consumer` prohibited | B-F03: broker visible and worker paused |
| B-F08 `consumer_slow` | Per-message processing delay of 100 ms in the consumer (about 10 msg/s against 20 produced) | `lab_kafka_consumer_lag_high`, plus `lab_consumer_processing_slow` | No action, human review; `resume_test_consumer` prohibited | B-F03: the worker is not paused and still processes |
| B-F09 `postgres_unavailable` | `ALTER DATABASE lab ALLOW_CONNECTIONS false` and existing sessions ended, for at most 420 s | `lab_postgres_down` (`pg_up` 0 for 15 s) | `restart_lab_dependency` on `lab-postgres`; `release_lab_db_blocker` prohibited | B-F01: the database is up and an exclusive lock is held |
| B-F10 `application_error` | A latent code path of the running release raises on every fourth item | `lab_api_error_ratio_high` | No action, human review; `rollback_demo_api` prohibited | B-F05: no new release; B-N01: stack traces in the logs |

- **Kafka proxy.** Every lab Kafka client (producer, consumer, lag exporter)
  reaches the broker through `lab-kafka-proxy`, because the broker advertises
  the proxy on its client listener; a separate internal listener serves the
  broker healthcheck and topic setup. An isolated experiment (throwaway
  network, same images) showed the exporter reporting `kafka_brokers 0`
  about 20 s after the partition, producer send errors at about one per 6 s,
  and full recovery (brokers 1, producer drains its backlog) within 10 s of
  the restore. The proxy accepts only the lab-internal token and exposes no
  metrics. The producer now counts and logs send failures instead of dying.
- **Restart targets.** `restart_lab_dependency` may target `lab-redis`,
  `lab-kafka` and `lab-postgres` (catalog `c3`); the lab controller keeps its
  own per-action target allowlist. Kafka and PostgreSQL report generations
  that a restart bumps.
- **Composite PostgreSQL revision.** `lab-postgres` now reports
  `pg-gen-<n>.lock-<digest|none>`: one approval is bound to both the server
  generation and the exact lock fixture (a digest of its token, so the token
  itself is not shown), and either changing makes it stale. The blocker
  release still releases only the fixture the approved revision names; its
  verification now checks the receipt's `revision_after` instead of `none`.
- **Verification per target.** `config/verification.yaml` keys may be
  `<action_type>:<target_id>`; they take precedence over a plain action key.
  The Redis restart spec moved to `restart_lab_dependency:lab-redis`; Kafka
  checks broker visibility and producer throughput, PostgreSQL checks `pg_up`
  and API errors. There is no generic restart spec, so an unexpected target
  verifies UNKNOWN.
- **No-action cases.** B-F08 and B-F10 have no safe lab remediation: resuming
  a running consumer or rolling back to the release that is already running
  cannot help. Their policy mapping is empty, so even a proposal is denied.
- **B0 rules.** The Kafka family (consumer, broker, producer incidents) reads
  broker count, producer send and error rates, consumer processing rate, lag
  and status. Broker visibility uses the latest sample, not the window
  maximum. The API family adds `lab-postgres` incidents, checks `pg_up` first
  and counts `Traceback` log lines (at least 3) to tell an application error
  from transient noise.
- **Not run live.** The running local demo uses a paid model for its
  investigation worker, so these scenarios were not injected into the shared
  lab; only the Kafka proxy was exercised in the isolated experiment above.
