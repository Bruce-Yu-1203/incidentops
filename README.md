# IncidentOps

Reliable, observable, policy-governed AI SRE runtime — **lab prototype, under
construction**. No benchmark results have been measured yet.

IncidentOps ingests alerts, lets a bounded read-only agent collect evidence,
and routes any proposed remediation through deterministic policy, exact human
approval, an isolated executor and an independent verifier. See:

- [Project constitution](constitute.md) — mandatory working rules
- [Requirements](docs/requirements.md) · [Architecture](docs/architecture.md) ·
  [Technology stack](docs/tech-stack.md) · [Roadmap](docs/roadmap.md) ·
  [Benchmark](docs/benchmark.md)
- [Architecture decision records](docs/adr/)

## Status

Phase P0 (contracts and Fault Lab foundation) is implemented: domain state
machines, contracts and action hashing, service catalog and role identities,
the Control PostgreSQL schema with compare-and-swap transitions, the
resettable Fault Lab with a controllable Kafka consumer, a web skeleton and
CI.

Phase P1 (real monitoring, alerts and incidents) is implemented: an
observability stack ([infra/README.md](infra/README.md)) with Prometheus rules
for lab API latency/5xx and Kafka consumer lag, an authenticated Alertmanager
webhook with transactional, race-safe incident deduplication per service, and
incident list/detail views. `make p1-gate` drives a Kafka consumer pause and a
PostgreSQL lock through real alerts to one active incident per service. The
remediation (P3) follows the [roadmap](docs/roadmap.md).

Phase P2 (read-only investigation) is implemented: six registered read tools
behind a fail-closed gateway with server-side query templates, budgets and
timeouts; redacted, digested evidence with provenance; a bounded LangGraph
agent with a schema-validated `submit_diagnosis`, PostgreSQL checkpoints and
OTel-correlated trajectory steps; a leased outbox worker; and investigation
views in the API and UI. `make p2-live` checks the tools against live lab
telemetry with a scripted model. The real-model gate (`make p2-gate`) needs a
provider key: set `INCIDENTOPS_MODEL_ID` and `INCIDENTOPS_ANTHROPIC_API_KEY` in
`.env`, then run `make api-run` and `make worker-run`.

Phase P3 (first safe remediation, B-F03) is implemented: a versioned
deterministic policy, immutable action envelopes bound by `action_hash`,
local users with sessions and RBAC, hash/version/TTL-checked approvals with
separation of duties, an isolated executor that revalidates before dispatch
and reconciles ambiguous outcomes by `action_id`, and an independent verifier
that alone may resolve an incident. Each process refuses other roles'
credentials. `make p3-gate` runs the live loop (approve → resume → verified
RESOLVED, and reject → zero lab mutations).

### Running the full loop locally

```bash
make lab-up infra-up seed-users
make api-run        # terminal 1 (API on 127.0.0.1:8000)
make control-run    # terminal 2 (policy evaluation, approval expiry)
make executor-run   # terminal 3 (the only remediation credential)
make verifier-run   # terminal 4 (independent recovery checks)
make worker-run     # terminal 5 (investigation; set INCIDENTOPS_MODEL_ID,
                    # or use rules-baseline-v1 for the no-key B0 baseline)
cd apps/web && INCIDENTOPS_API_URL=http://127.0.0.1:8000 pnpm dev   # UI on :3001
```

## Development quick start

Requirements (tested on macOS 15+, Apple silicon): [uv](https://docs.astral.sh/uv/)
≥ 0.5, Python 3.12, Node.js ≥ 20.9 with pnpm, Docker with Compose v2
(4 CPUs / 4 GB for Docker is enough for P0).

```bash
make env          # create .env with fresh random service tokens
make install      # Python environment from uv.lock
make web-install  # web dependencies from pnpm-lock.yaml
make db-up        # Control PostgreSQL on 127.0.0.1:5433
make check        # lint, format, mypy, pytest (incl. real-Postgres tests), web checks
make migrate      # apply Alembic migrations to the control database
```

Fault Lab (see [fault-lab/README.md](fault-lab/README.md)):

```bash
make lab-up       # start lab services and wait for health checks
make lab-smoke    # Phase 0 gate: progress, pause, reset, role separation
make lab-reset    # restore the healthy baseline
make lab-down     # stop the lab and delete its volumes
```

Every change follows [constitute.md](constitute.md): one conventional commit
per change, tests for every change, and a green `make check` before handoff.
