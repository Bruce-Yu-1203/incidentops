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
read-only agent (P2) and remediation (P3) follow the [roadmap](docs/roadmap.md).

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
