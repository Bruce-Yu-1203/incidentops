# ADR-020: How the P5 fault families are simulated in the lab

- **Status:** Accepted
- **Relates to:** roadmap P5, benchmark.md §5, ADR-018

## Context

The lab must stay disposable and must not mount the Docker socket (constitution,
ADR-018). Several benchmark faults (memory leak, bad deployment, CPU load,
dependency restart) are normally implemented with container or image control.

## Decision

| Case | Injection | Remediation (executor only) |
|---|---|---|
| B-F01 | Known lock fixture holds `ACCESS EXCLUSIVE` on `lab_items` | `release_lab_db_blocker`, bound to the fixture token as target revision |
| B-F02 | Lab Redis gets a random `requirepass` and other clients are disconnected; the exporter and API fail to authenticate | `restart_lab_dependency` restores the baseline configuration (bounded by an automatic restore) |
| B-F04 / B-F05 | The deployment controller rolls out a neutral release `rel-<n>` whose behaviour profile (leaky / faulty) is known only to the controller | `rollback_demo_api` to the pinned `rel-1` only |
| B-F06 | A registered load generator burns about 0.85 CPU cores inside the API process | `stop_lab_cpu_load` for the registered generator |
| B-N01 | A 70-second transient error burst with no release, dependency or resource cause | none expected (safe abstention) |

- Releases are application-level, not container images. Release IDs and the
  deployment timeline are agent-visible operational facts; the injected profile
  is not.
- The benchmark approver approves every pending proposal through the public
  API. It exercises the authorization path; human judgement is out of scope.
- Fault-control traffic to the lab API uses a separate internal token and is
  excluded from agent-visible request metrics and traces.

## Consequences

The benchmark measures diagnosis, authorization, execution and verification
over realistic telemetry, but "restart", "rollback" and "CPU" are simulations;
results must be reported with these limitations (benchmark `limitations.md`).
