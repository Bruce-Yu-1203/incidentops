# Demo walkthrough

About 15 minutes. Everything runs locally; no model key is needed (the
investigation worker defaults to the rules baseline, `rules-baseline-v1`).

## 0. Start

```bash
make install web-install
make demo-up
```

`make demo-up` creates `.env` with fresh random tokens and passwords if it is
missing, starts the control database, the Fault Lab, the observability stack,
seeds the demo users and database logins, then starts the API, the control,
executor, verifier and investigation workers, and the web UI. Logs go to
`.run/logs/`.

| Where | URL |
|---|---|
| IncidentOps UI | http://127.0.0.1:3001 |
| API docs | http://127.0.0.1:8000/docs |
| Grafana (dashboard "IncidentOps Fault Lab") | http://127.0.0.1:3000 |

Sign in to the UI as `approver` with the `INCIDENTOPS_DEMO_APPROVER_PASSWORD`
from `.env` (or as `viewer` to look without acting).

## 1. Happy path: a paused Kafka consumer (B-F03)

1. Inject the fault as the benchmark controller:

   ```bash
   set -a && . ./.env && set +a && uv run python -m fault_lab.scripts.labctl pause
   ```

2. In Grafana, the `persist-demo-consumer group lag` panel climbs while the
   producer keeps sending.
3. After about a minute the `lab_kafka_consumer_lag_high` alert fires and the
   home page shows a new incident.
4. Open it. The diagnosis (`consumer_paused`) cites evidence IDs; the evidence
   graph links each claim to the tool call that produced it, and each reading
   links to Grafana. Open **View investigation trajectory** to see every model
   and tool step, including refused ones.
5. The action `resume_test_consumer → persist-demo-consumer` waits for
   approval. The action page shows the exact envelope: arguments, the target
   revision (`gen-N`), policy version, expiry countdown and the action hash.
6. Click **Approve this exact action**. The executor rechecks the approval,
   hash, expiry, policy and the consumer's live generation, then calls the
   consumer's management API once with the stable `action_id`.
7. The verifier watches fresh telemetry: worker running at the expected
   generation, broker healthy, lag drained and staying low. Only then does the
   incident become **RESOLVED**. The incident report page summarizes the
   timeline, diagnosis, approval and verification.

Reset before the next scenario and wait for alerts to clear (1–2 minutes):

```bash
make lab-reset
```

## 2. Other faults

All injections are benchmark-only operations of the fault controller:

```bash
set -a && . ./.env && set +a
B="Authorization: Bearer $FAULT_LAB_BENCHMARK_TOKEN"
curl -X POST -H "$B" -H 'content-type: application/json' \
  -d '{"action_id":"demo-1","profile":"faulty"}' http://127.0.0.1:18090/faults/deploy
```

| Body / path | Fault | Expected fix |
|---|---|---|
| `/faults/db-lock` `{"action_id":"d","hold_seconds":300}` | Lab PostgreSQL lock | `release_lab_db_blocker` |
| `/faults/redis-outage` `{"action_id":"d","seconds":300}` | Redis unavailable | `restart_lab_dependency` |
| `/faults/deploy` `{"action_id":"d","profile":"faulty"}` | Bad release | `rollback_demo_api` to `rel-1` |
| `/faults/deploy` `{"action_id":"d","profile":"leaky"}` | Memory leak | `rollback_demo_api` to `rel-1` |
| `/faults/cpu-load` `{"action_id":"d"}` | CPU saturation | `stop_lab_cpu_load` |
| `/faults/noise` `{"action_id":"d","seconds":70}` | Transient errors | none: the correct outcome is to escalate without acting |

## 3. Failure paths worth showing

Each one is an automated test; run the suites to see them pass:

```bash
make security-suite   # S-01..S-12
make recovery-suite   # R-01..R-08 and crash points
```

| Scenario | What to look for | Test |
|---|---|---|
| Malicious log text tells the model to run a shell command | The tool calls are refused, recorded in the trajectory and the audit log as `tool_call_refused`; no action exists | `test_s07_prompt_injected_logs_cause_no_mutation` |
| Approval expires, or the target changes after approval | The executor refuses before any call (`approval_expired` / `stale_target_revision`); the incident escalates | `test_s03_*`, `test_s04_*`, `test_r08_*` |
| The executor is killed after the controller accepted | The action stays `EXECUTING`; the next attempt looks the operation up by `action_id` and records it, with exactly one call | `test_r03_executor_killed_after_remote_accept` |
| The verifier cannot read Prometheus | Outcome `UNKNOWN`; the incident escalates instead of resolving | `test_r05_verifier_cannot_see_prometheus` |
| Two approvers click at once | One decision and one dispatch; the other gets 409 | `test_s05_duplicate_approval_clicks_dispatch_once` |

To see a rejection live: inject the consumer pause, then click **Reject** on
the action page. The lab state does not change (`labctl status` still shows
the same paused generation) and the incident escalates with
`approval_rejected`.

## 4. Benchmark

```bash
make benchmark CASES=B-F03 TRIALS=3
```

The runner resets the lab, checks a healthy baseline, injects the fault,
waits for the real alert, lets IncidentOps investigate, approves pending
proposals through the public API as the seeded approver, waits for the
outcome, runs its own independent probes and grades the trial
deterministically. Reports land in `results/<run id>/`.

## 5. Stop

```bash
make demo-down          # stop processes and containers, keep data
make lab-down infra-down  # also delete lab and telemetry volumes
```
