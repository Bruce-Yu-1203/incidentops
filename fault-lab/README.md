# Fault Lab

Disposable, resettable data plane that IncidentOps monitors. It is **never** a
production system and stores no IncidentOps workflow state.

## Topology (P0)

| Service | Purpose | Host port |
|---|---|---|
| `lab-postgres` | Lab PostgreSQL (fault target; separate from Control PostgreSQL) | — |
| `lab-redis` | Lab Redis cache | — |
| `lab-kafka` + `kafka-init` | Single-node KRaft broker, topic `demo.events.v1` (3 partitions) | — |
| `lab-api` | Instrumented demo API (`/healthz`, `/readyz`, `/events/count`, `/metrics`) | 18080 |
| `lab-consumer` | Controllable consumer `persist-demo-consumer` with management API | 18081 |
| `demo-producer` | Seeded fixed-rate producer (`LAB_PRODUCER_RATE`, default 20 msg/s) | 18082 |

All host ports bind to `127.0.0.1`. No service mounts the Docker socket.
Third-party images and the Python base image are pinned by digest; Python
dependencies install from the hashed `requirements.lock`.

## Consumer management API (ADR-009, ADR-010)

| Route | Allowed role |
|---|---|
| `GET /control/status` | benchmark, executor, reader |
| `POST /control/pause` `{action_id, expected_generation}` | benchmark only |
| `POST /control/resume` `{action_id, desired_paused: false, expected_generation}` | executor only |
| `POST /control/reset` `{action_id}` | benchmark only |
| `GET /control/operations/{action_id}` | benchmark, executor, reader |

Each role has its own bearer token (`FAULT_LAB_BENCHMARK_TOKEN`,
`FAULT_LAB_EXECUTOR_TOKEN`, `FAULT_LAB_READ_TOKEN`). The investigation agent may
only ever hold the read token.

## Running

Tested on macOS 15+ (Apple silicon) with Docker Desktop, 4 CPUs and 4 GB of
memory assigned to Docker. From the repository root:

```bash
make env        # create .env with fresh random tokens (once)
make lab-up     # build and start; waits until every service is healthy
make lab-reset  # restore the healthy baseline (consumer unpaused, new generation)
make lab-smoke  # Phase 0 gate (see below)
make lab-down   # stop and delete lab volumes
```

**Readiness:** `make lab-up` returns only after every health check passes:
`pg_isready`, `redis-cli ping`, Kafka API versions, the topic-creation job
exiting successfully, `lab-api /readyz` (PostgreSQL + Redis reachable) and the
consumer/producer `/healthz` endpoints.

**Phase 0 gate (`make lab-smoke`):** the consumer's processed counter grows
while unpaused; executor and read-only tokens receive 403 on pause; after a
benchmark pause the counter stays constant while the producer keeps sending;
after reset the consumer is unpaused and progressing again.
