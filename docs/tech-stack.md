# IncidentOps — Technology Stack and Engineering Standards

**Version:** 2.0 · **Status:** Proposed / not yet implemented · **Date:** 2026-11-09  
**Related:** [Requirements](requirements.md) · [Architecture](architecture.md) · [Roadmap](roadmap.md) · [Benchmark](benchmark.md)

## 1. Technology Selection Principles

Each dependency must solve a defined engineering need. Prefer mature components for telemetry storage, graph orchestration, and routine infrastructure, while implementing the project-specific **security, evidence, approval, execution, and reliability boundaries** in application code.

Priorities, in order:

1. **Safety isolation:** Investigation agents cannot mutate monitored services.
2. **Reproducibility:** A fresh developer machine should run a real lab and reset faults.
3. **Reliability:** Explicit transactions, operation IDs, recoverable workflows, and failure states.
4. **Testability:** Dependency boundaries allow deterministic unit and real-system integration tests.
5. **Scope discipline:** No technology added solely to increase résumé keyword count.
6. **Model portability:** A provider adapter and recorded model version make tests comparable.

**Important:** Recommended version families below are starting points, **not guarantees of latest availability**. Freeze exact tested versions in lockfiles/container digests during Phase 0 and document any incompatibilities.

## 2. Chosen Stack

| Layer | Recommended | Why / boundaries |
|---|---|---|
| Language — control plane | **Python 3.12+** | Strong async/API, data tooling, model ecosystem |
| API | **FastAPI + Pydantic v2** | Typed HTTP APIs, validation, OpenAPI, async I/O |
| Agent graph | **LangGraph** | Explicit bounded investigation state, checkpointing, graph nodes |
| LLM adapter | Provider-neutral `ModelAdapter` | Tool-call/structured-response handling, limits, version recording |
| Domain persistence | **PostgreSQL** | Incident, action, approval, audit, outbox transactional source of truth |
| ORM and schema migration | **SQLAlchemy 2 + Alembic** | Transaction control, migrations, typed repositories |
| Graph persistence | **PostgreSQL LangGraph checkpointer** | Durable state for investigation runs; not external exactly-once |
| Job transport (MVP) | **PostgreSQL outbox + leased jobs** | Avoid new broker in control plane; atomic job creation |
| Frontend | **Next.js + React + TypeScript** | Incident/trajectory/approval UI, typed client |
| Infrastructure instrumentation | **OpenTelemetry SDK + Collector** | Correlate API/backend traces, logs, metrics as supported |
| Metrics + alerting | **Prometheus + Alertmanager** | Scraping/exporters, PromQL, real alerts |
| Log storage | **Loki** | Bounded service/time log search |
| Trace storage | **Tempo** | Distributed traces and correlation; Jaeger viable alternative |
| Telemetry UI | **Grafana** | Graphs; IncidentOps UI is workflow-focused |
| PostgreSQL exporter | **postgres_exporter** + app pool metrics | DB health and separate application connection-pool telemetry |
| Redis exporter | **redis_exporter** | Memory, evictions, clients, command rates |
| Kafka monitoring | **Kafka JMX Exporter** + group-lag source | Broker/client metrics and per-group backlog |
| Host/container | **node_exporter + cAdvisor** | CPU/memory/disk/container usage and restarts as supported |
| Lab orchestration | **Docker Compose** | Repeatable integration and fault injection; no Kubernetes required |
| Controlled remediation | Restricted **HTTP management APIs** + Docker-aware lab deployment controller | Exact allowlisted, auditable operations; no LLM shell |
| Tests | **pytest, pytest-asyncio, httpx, Playwright** | Unit/integration/recovery/E2E, browser approval flows |
| Quality gates | **ruff, pyright/mypy, ESLint, TypeScript strict, GitHub Actions** | Reproducible enforcement |

**Split databases:** The **Control PostgreSQL** is the authoritative workflow store and should not be impacted by a benchmark fault injected into the **Lab PostgreSQL**. Treat them as separate containers/databases and credentials even if development uses one host.

## 3. Framework vs. Custom Runtime Responsibilities

### 3.1 Use LangGraph for

- Investigation graph structure: context → model → read tools → evidence → validated diagnosis.
- Conditional routing, iteration limits, checkpoint/resume, and optional later subgraphs.
- Safe state persistence of **agent computation** after defined graph transitions.
- Optional lightweight model/tool instrumentation hooks, coordinated with OpenTelemetry.

### 3.2 Implement in IncidentOps code

| Custom module | Why it is **not** delegated to LangGraph |
|---|---|
| `RunManager` | Stable run IDs, incident version binding, budgets, cancellation, scheduling |
| `ContextBuilder` | Scoped evidence summaries, token caps, redaction, prompt versions |
| `ModelAdapter` | Provider isolation, response/schema validation, retry policy, usage metadata |
| `ToolRegistry` | Immutable manifests, typed schemas, per-tool limits and scopes |
| `ToolGateway` | Mandatory authentication, authorization, timeouts, bounded read execution |
| `EvidenceStore` | Source provenance, digests, snapshots, hypothesis/evidence linkage |
| `TrajectoryRecorder` | Domain-level agent/tool steps linked to infrastructure traces |
| `PolicyEngine` | Deterministic allow/deny/approval rules; **never** model judgement |
| `ApprovalService` | Human identity, expiry, exact action hash, separation of duties |
| `ActionExecutor` | Separate credentials, external operation IDs, retries/reconciliation |
| `RecoveryVerifier` | Fresh telemetry and domain-specific recovery checks |
| `AuditService` | Security events from trusted code paths |
| `BenchmarkRunner` | Ground truth, reset, injection, scoring, safety and crash suites |

The agent only sees **read-only** tool specifications. A proposed mutation is an output artifact (`ActionProposal`), not a direct LLM-controlled connector call.

**Why not build an entire Runtime from scratch?** It would consume effort in graph scheduling, checkpoint serialization, and generic state management without demonstrating additional SRE product value. A small minimal ReAct baseline is optional for learning/testing only; it must not become a duplicate production engine.

## 4. Control Plane: Python, PostgreSQL, Workers

### 4.1 FastAPI / Pydantic

- Make API contracts explicit: `AlertEvent`, `EvidenceRecord`, `Diagnosis`, `ActionProposal`, `ApprovalDecision`, `ActionReceipt`, `VerificationResult`, `AuditEvent`.
- Reject unknown critical action fields; strict canonical validation before computing action hashes.
- Keep request handlers short: validate/authenticate → transaction → durable outbox; let workers perform investigation/external operations.
- Use distinct service identities for webhook ingestion, investigation, approval API, executor, and benchmark controller.

### 4.2 PostgreSQL data and transactional constraints

- Use transactions for dedup upsert + incident/job creation, and separately for approval decision + audit + dispatch job.
- Define partial unique constraints for active dedup keys as needed; test `ON CONFLICT` semantics with real Postgres.
- Claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED` and a lease expiration or equivalent.
- Use compare-and-swap (`state`/`version`) for every action/incident transition.
- Store immutable `action_hash`, policy version, exact normalized arguments, expected target revision; constrain one execution per logical action ID.
- Separate telemetry snapshots from state tables; reference raw telemetry in specialized stores.
- Back up/recreate control data and schema migrations independently from disposable Fault Lab state.

**Do not describe this as exactly-once processing.** Leased jobs are at-least-once; external side effects require target-state idempotence and reconciliation.

### 4.3 Worker topology

| Worker | Permissions | Notes |
|---|---|---|
| Investigation worker | Read telemetry + write own evidence/checkpoint | No mutation endpoints or Docker socket |
| Action executor | Read approved action + scoped management API | No LLM access; must revalidate |
| Recovery verifier | Read latest service metrics and state | Separate from executor outcome |
| Reconciler | Query external operation status | Does not blindly resend irreversible action |
| Benchmark controller (test-only) | Inject/reset fixtures | Not deployed into normal application plane |

These can initially share source code and runtime image but require **different process identities / credentials**, especially the executor and benchmark controller.

## 5. Telemetry Stack and Diagnostic Contract

### 5.1 Prometheus + Alertmanager

Use Prometheus to ingest quantitative metrics and Alertmanager to send authenticated alert webhooks. Common alerts:

- API `5xx` ratio, P95 latency, request throughput.
- PostgreSQL active connections, locks, transaction/slow query proxies, app-pool wait time.
- Redis memory pressure, key evictions, response latency and client count.
- Kafka partition/group lag, producer rate, consumer rate, broker status.
- Container CPU, memory working set, restarts, readiness, OOM.

**Avoid assuming a universal metric name.** Exporter and Kafka lag metric naming differ between versions. Maintain query templates (`template_id`) and validate each template using fixture data. Application-pool wait time may need custom app metrics; `postgres_exporter` alone cannot provide it.

### 5.2 OpenTelemetry, Loki, Tempo

- Instrument lab API and workers with trace correlation IDs.
- Use the Collector for receiving and routing telemetry to backends supported by each signal pipeline.
- Store logs in Loki; choose bounded LogQL templates and redact content before model use.
- Store traces in Tempo; link `trace_id` from API requests to agent evidence.
- Instrument **IncidentOps** itself: API latency/error, queue depth/lease age, model/tool duration, policy denials, approval backlog, reconciliation count, verifier failures.
- OTel GenAI semantic conventions can guide agent/model/tool spans; use well-scoped custom attributes for domain IDs where conventions are insufficient. Keep compatibility checked against selected instrumentation versions.

### 5.3 Component adapters

| Data source | Access method | Evidence | Common pitfalls |
|---|---|---|---|
| PostgreSQL | Exporter + scoped read-only `pg_stat_*` | connections, locks, wait state | app pool != server connections; no arbitrary SQL |
| Redis | Exporter or allowlisted `INFO` | memory, evictions, latency | memory pressure not proof of specific user impact |
| Kafka | JMX exporter + lag source + test worker status | lag, rate, broker, paused flag | broker does not expose universal group pause/resume API |
| API | OTel and app metrics | error rate, P95, dependency spans | sampling/incomplete traces, baseline variation |
| Docker/host | cAdvisor/node_exporter/health API | CPU, memory, restart | privileged socket security, metric availability |
| Deployment events | Versioned test deployment controller | current/previous image digest, rollout time | correlation != causation |

## 6. Remediation Implementation: What Really Executes?

The first action, `resume_test_consumer`, is invoked through a **protected worker management API**. The worker manages its own polling/paused state. The command includes:

```json
{
  "action_id": "act-32",
  "consumer_id": "persist-demo-consumer",
  "desired_paused": false,
  "expected_generation": "gen-8"
}
```

The management API must:

1. Authenticate the executor and confirm the registered consumer ID.
2. Atomically reject a stale generation or conflicting command.
3. Persist/expose an operation receipt keyed by `action_id` for reconciliation.
4. Converge to `paused=false` without creating duplicate side effects on repeat.
5. Expose read-only `status` and operation lookup endpoints to authorized clients.

For later `rollback_demo_api`, **Docker Engine/Compose alone does not define a business rollback service**. Implement a deployment controller that maps a registered service to a known image digest/version, re-creates the registered container, and exposes `GET /operations/{action_id}`. Never accept arbitrary image strings/command arguments from LLM output. Since Docker socket access can imply host-level privilege, confine controller access to a disposable isolated environment; for public deployments consider a dedicated sandbox/VM rather than giving web API services Docker access.

For `release_lab_db_blocker`, control a **known injected fixture transaction** by its test token. Do not allow arbitrary PostgreSQL backend termination. For `stop_lab_cpu_load`, control only a dedicated generator process/container.

## 7. Authentication, Authorization, and Governance

- **Humans:** session-based login with server-side RBAC (`Viewer`, `Operator`, `Approver`, `Administrator`). Seed identities locally for demo/CI; choose a maintained auth provider before internet exposure.
- **Services:** separate authenticated identities for Alertmanager webhook, tool gateway adapters, executor, and test controller.
- **Policy:** deterministic Python rule functions with versioned config initially. Use OPA only if policy sharing/complexity becomes material.
- **Approvals:** authenticated humans approve the exact canonical action hash, TTL and expected target revision. Mutation is rechecked immediately before dispatch.
- **Auditing:** append-only logical events, stable correlation IDs, limited DB write role, and optionally hash-chain integrity checks. Avoid claiming absolute immutability.
- **Prompt injection:** treat logs/metric labels/trace attrs as data. Model instructions do not grant privileges. Redact before storing/modeling; budget/egress controls are required.

## 8. Failure, Checkpointing and Recovery

| Failure point | Recovery owner | Behavior |
|---|---|---|
| Model timeout | Model adapter / RunManager | bounded retry or `FAILED`, preserve partial evidence |
| LangGraph worker killed | Checkpointer / leased worker | resume committed read-only work; revalidate incident version |
| Approval API duplicates | ApprovalService + Postgres | atomic transition, one outbox dispatch |
| Action worker dies before remote call | Executor lease | safe claim retry after inspecting action status |
| Remote accepted but reply lost | Reconciler | operation lookup using `action_id`; no blind write replay |
| Verification source unavailable | Verifier | report `UNKNOWN`/escalate; never mark resolved |
| Policy service unavailable | All gateways | fail closed on write attempts |

**Checkpoint ≠ external side-effect transaction.** Graph resume may rerun node code, therefore do not hide mutating operations inside a freely replayed LangGraph node. Use a separate action subsystem with explicit transaction and external operation semantics.

## 9. Model Selection and Evaluation Discipline

- Adopt a provider-neutral adapter accepting configured model ID, temperature, tool-call format, max output tokens, timeout, and credentials.
- For benchmark reproducibility, record exact model ID/build if exposed, prompt version, parameters, tool manifests, provider rate/timeout config, hardware/deployment profile, and date.
- For cost, report both token counts and billed/model-provider cost when known; do not infer an unknown price.
- Use schema validation for final diagnosis and action proposals; invalid JSON/schema is an evaluated failure or bounded repair attempt, not silently accepted.
- The model never gets fault labels/ground-truth files or the benchmark control API.

## 10. Repository Layout

```text
incidentops/
├── README.md
├── docs/
│   ├── requirements.md
│   ├── architecture.md
│   ├── tech-stack.md
│   ├── roadmap.md
│   └── benchmark.md
├── apps/
│   ├── api/                        # FastAPI HTTP and approval interface
│   ├── web/                        # Next.js dashboard
│   ├── investigation-worker/       # LangGraph jobs, read-only permissions
│   ├── executor/                   # approval-bound action execution
│   └── verifier/                   # independent post-action checks
├── incidentops/
│   ├── domain/                     # incident/action state machines
│   ├── agent/                      # LangGraph, context, model adapter
│   ├── tools/                      # registry, gateway, read adapters
│   ├── evidence/                   # provenance and redaction
│   ├── trajectory/                 # step records and OTel hooks
│   ├── policy/                     # deterministic rules
│   ├── approval/                   # human decisions
│   ├── execution/                  # controller adapters, reconciliation
│   ├── verification/               # independent rules
│   ├── audit/                      # trusted event emission
│   ├── persistence/                # SQLAlchemy, repositories, migrations
│   └── evaluation/                 # graders, result exports, baselines
├── fault-lab/
│   ├── compose.yaml
│   ├── services/                   # API + worker + dependencies
│   ├── controllers/                # fault and deployment controllers
│   ├── scenarios/                  # ground truth, invisible to agent
│   └── scripts/                    # inject, reset, verify
├── infra/
│   ├── compose.yaml
│   ├── prometheus/                # rules + config
│   ├── loki/
│   ├── tempo/
│   └── otel/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   ├── security/
│   ├── recovery/
│   └── e2e/
└── .github/workflows/
```

Logical module placement can be consolidated; the crucial invariant is **credential and execution separation**, not deploying dozens of services.

## 11. Development, Test and Deployment Profiles

| Profile | Runs | Allowed actions |
|---|---|---|
| `dev` | Control plane + small Fault Lab + basic telemetry | Manual lab-only; seeded users |
| `benchmark` | Resettable lab + model adapter + test approver + grader | Controlled injected faults, recorded trials |
| `demo` | Stable dashboard, read-only Grafana links, one approved recovery | Limited disposable target |
| `staging-optional` | External LivePulse staging observability | Read-only initially; separate explicit policy before writes |
| `production` | Not an MVP target | **No remediation support/credentials** |

Do not inject load/stress against Railway production or any user-facing deployment. Fault tests must run on disposable lab resources, preferably isolated from developer/host-critical data.

## 12. Engineering Standards and Release Gates

- **Static:** Python lint/type checks, TS strict, schema/migration consistency, dependency scanning.
- **Unit:** state transition legality, canonical action hash, authorization, redaction, scoring math.
- **Contract:** tool schema, management API, exporter metric names and labels, webhook payload normalization.
- **Integration:** real Postgres concurrency/outbox transactions; real Kafka lag and consumer pause/resume; telemetry source queries.
- **Security:** unauthorized writes, stale/modified approval, prompt injection, off-scope target, no audit omission.
- **Recovery:** crash after approval, before/after remote dispatch, during verification; ambiguous status lookup.
- **E2E:** user can inspect evidence, approve a valid action, observe execution, verification, and audit.
- **Benchmark:** deterministic grading, six scenarios and negative cases; trials/invalid-rate documented.

Fail release on any reproducible unauthorized mutation, unapproved execute path, missing audit evidence for a mutation, or benchmark ground-truth leakage.

## 13. Deliberately Deferred Technologies

| Technology | Why defer | Revisit only if |
|---|---|---|
| Multi-agent | Cost and coordination without baseline | Matched benchmark shows measurable advantage |
| Temporal | Another durable orchestration system on top of Postgres/LangGraph | Complexity or throughput requires it |
| Kafka in **control plane** | Outbox/leased jobs suffice for prototype | Measured job-delivery bottleneck |
| Kubernetes | Extra permissions/cluster scope | Separate well-isolated cluster demonstrator |
| ClickHouse / OpenSearch | No need for internal telemetry warehouse | Retention/query demand justifies it |
| Vector DB / long-term agent memory | No validated retrieval corpus | Later runbook-search product requirement |
| OPA | Simple versioned deterministic policy is sufficient | Multi-service policy administration required |
| Auto-reflection / self-tuning | Outside product scope | **No planned inclusion** |

## 14. Reference Documentation

Primary documentation useful when implementing this design:

- LangGraph persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- LangGraph interrupts: https://docs.langchain.com/oss/python/langgraph/interrupts
- Prometheus: https://prometheus.io/docs/prometheus/latest/overview/
- Alertmanager: https://prometheus.io/docs/alerting/latest/alertmanager/
- OpenTelemetry: https://opentelemetry.io/docs/
- OpenTelemetry GenAI conventions: https://opentelemetry.io/docs/specs/semconv/gen-ai/
- Loki: https://grafana.com/docs/loki/latest/
- Tempo: https://grafana.com/docs/tempo/latest/
- Kafka monitoring: https://kafka.apache.org/documentation/#monitoring
- PostgreSQL monitoring statistics: https://www.postgresql.org/docs/current/monitoring-stats.html
- Docker Engine security: https://docs.docker.com/engine/security/
- SQLAlchemy ORM: https://docs.sqlalchemy.org/en/20/

Links document the intended integration points. Verify exact versions and APIs when implementation begins.
