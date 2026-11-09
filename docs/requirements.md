# IncidentOps — Product Requirements Document

**Version:** 2.0 (implementation specification)  
**Status:** Proposed / not yet implemented  
**Date:** 2026-11-09  
**Project:** IncidentOps — Reliable, Observable, Policy-Governed AI SRE Runtime  
**Companion documents:** [Architecture](architecture.md) · [Technology Stack](tech-stack.md) · [Roadmap](roadmap.md) · [Benchmark](benchmark.md)

## 1. Product Summary

IncidentOps is an AI-assisted incident investigation and **controlled remediation runtime** for backend and distributed systems. It sits **on top of**, rather than replacing, existing observability infrastructure. It ingests alerts; lets a bounded, read-only agent investigate evidence from metrics, logs, traces, and deployment history; produces an evidence-linked diagnosis and an optional typed action proposal; and uses deterministic policy, durable human approval, an isolated executor, and independent verification before marking an incident resolved.

**Problem addressed:** A plausible AI diagnosis is not the same as a safe operational action. The key product contribution is making an AI-driven incident response **traceable, authorized, recoverable, and independently testable**.

**First target:** A self-contained Docker Compose **Fault Lab** with an instrumented API, a controllable Kafka consumer, PostgreSQL, Redis, and host/container telemetry. An isolated LivePulse staging integration may be added later. Production remediation is prohibited in the MVP.

**First complete vertical slice:** Pause an instrumented Kafka test consumer → observe increasing consumer lag → ingest a real alert → agent gathers read-only evidence → propose `resume_test_consumer` → policy check → human approval → executor calls the scoped consumer management API → verification observes consumer status and lag recovery → publish an auditable report.

An earlier **read-only slice** (API latency/DB lock → alert → evidence-backed diagnosis) is used to bring up the telemetry and agent path before implementing mutation.

## 2. Positioning and Differentiation

The project is **not** an alternative to Datadog, Grafana, PagerDuty, or ServiceNow. It does not aim to invent alert aggregation, a general monitoring database, or autonomous root-cause analysis as a new industry category. It deliberately explores these engineering properties:

1. **Evidence-linked investigation:** Every diagnosis refers to queryable, timestamped evidence and observable tool steps.
2. **Deterministic safety boundary:** The LLM has no remediation credentials; authorization and approval are checked in application code and again in the executor.
3. **Durable action lifecycle:** Crashes, duplicate deliveries, retries, stale approvals, and unknown external outcomes are modeled explicitly.
4. **Verifiable recovery:** A successful external API response is not proof that an incident was resolved.
5. **Reproducible evaluation:** A separate Fault Lab and deterministic graders test diagnosis, remediation, safety, and recovery.

These are **project engineering emphases**, not claims that commercial systems lack these capabilities.

## 3. Goals, Non-Goals, and Constraints

### 3.1 Goals

- Ingest, authenticate, deduplicate, correlate, and persist operational alerts.
- Investigate API, PostgreSQL, Redis, Kafka, and Docker/host failures from **real telemetry**, without granting the agent arbitrary infrastructure access.
- Produce typed diagnoses with explicit uncertainty, supporting/contradicting evidence IDs, and proposed next steps.
- Record agent/tool **trajectories**, infrastructure trace links, policy decisions, and durable security audit events.
- Require a human to authorize each allowlisted write action against its exact immutable parameters and current target revision.
- Run allowlisted lab remediation from a separate, least-privileged execution process; reconcile ambiguous outcomes.
- Verify post-action health using new observations and declared per-scenario success conditions.
- Enable single-command, resettable fault injection and produce machine-readable benchmark reports.
- Remain buildable by one developer with a modular monolith, isolated workers, and bounded dependencies.

### 3.2 Non-Goals

- **No agent self-optimization:** no automatically edited prompts, self-promoted memories/skills, autonomous policy changes, or learned strategy promotion.
- No unrestricted shell, arbitrary SQL mutation, dynamic scripts, arbitrary Docker container selection, or production-write credentials for the agent.
- No automatic **production** rollback, scaling, restarting, or cache deletion in the MVP.
- No monitoring time-series database, dashboard replacement for Grafana, or broad enterprise alert ingestion platform.
- No mandate to build a full custom ReAct scheduler from scratch; LangGraph is the default investigation graph engine.
- No multi-agent requirement at initial release. Special-purpose read-only subagents are an optional experiment **after** establishing a fair single-agent baseline.
- No assertion of exactly-once **external side effects** from PostgreSQL checkpoints or a queue alone.
- No arbitrary cloud/Kubernetes access, general runbook execution platform, or LLM-as-Judge-only benchmark.

### 3.3 Trust and Deployment Assumptions

- A separate **control plane** runs IncidentOps; the **monitored plane** runs disposable lab services and collectors.
- The model sees only scoped, redacted observational data; logs and metrics metadata are untrusted input.
- Ground-truth fault labels and expected actions live in the benchmark runner, **not** in agent prompts or accessible production tool responses.
- All remediation is lab-only by default; staging support requires a distinct configuration and identity boundary.
- External providers may be temporarily unavailable or respond ambiguously; an unknown outcome is a valid, explicit state.

## 4. Personas and Permissions

| Persona | Primary task | Allowed operations |
|---|---|---|
| Viewer | Inspect incident reports, evidence, traces | Authorized read-only UI/API |
| Operator | Investigate, request remediation | Read + create proposal; no implicit approval |
| Approver | Review exact proposed action | Authorized approve/reject; cannot override policy |
| Administrator | Configure service catalog and policies | Manage configuration; no direct hidden bypass |
| Investigation agent | Gather evidence and propose | Scoped read-only tools; no executor credentials |
| Executor service | Perform an already-authorized action | Narrow lab controller credentials; no LLM decisions |
| Benchmark controller | Inject/reset faults and grade outcomes | Separate test-only privileges; unavailable to agent |

**Separation of duties:** Support a rule forbidding a proposer from approving their own action. An approver's click is insufficient without fresh executor-side policy and precondition checks.

## 5. Delivery Scope

| Release | Included | Excluded |
|---|---|---|
| **M0: Investigation slice** | Lab, alert ingestion, read-only metrics/logs/traces, incident, structured diagnosis, trajectory | Remediation |
| **M1: End-to-end MVP** | Kafka consumer pause/resume, immutable action, policy, approval, executor, verifier, audit, durable work | Other write actions, multi-agent |
| **M2: Reliability + benchmark release** | Rollback and other controlled fault cases, reconciliation, crash tests, security matrix, published comparative results | Production action |
| **M3: Optional** | LivePulse staging adapter, specialist subagents, more deployment targets | Self-optimization |

## 6. Functional Requirements

**Priorities:** P0 = required to ship M1; P1 = required to ship M2; P2 = optional. Every acceptance criterion is testable; associated benchmark IDs are detailed in `benchmark.md`.

### 6.1 Alert Ingestion and Incident State

| ID | Priority | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-001 | P0 | Authenticate and validate Alertmanager webhooks | Unauthorized/malformed payloads rejected; accepted payloads persisted |
| FR-002 | P0 | Deduplicate repeated alerts and preserve source events | Replayed fingerprints create no second active incident; event retained |
| FR-003 | P0 | Use an explicit incident lifecycle and versioned transitions | Illegal/concurrent transitions fail atomically |
| FR-004 | P0 | Persist incident, alert, and job in a transactional boundary | API/worker restart cannot orphan accepted incident work |
| FR-005 | P0 | Correlate severity, service, environment, and time window | Each incident has a stable identity and searchable timeline |

### 6.2 Telemetry, Tools, and Evidence

| ID | Priority | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-006 | P0 | Query allowlisted Prometheus metric templates | Fixed service/time/series/result budgets enforced |
| FR-007 | P0 | Query bounded Loki logs and Tempo traces | Results redacted and source-linked; tool cannot query arbitrary targets |
| FR-008 | P0 | Provide typed PostgreSQL, Redis, Kafka and container diagnostics | Each subsystem has at least one real lab query and integration test |
| FR-009 | P0 | Include test deployment/change metadata | Agent can inspect deployment timeline without learning test answer |
| FR-010 | P0 | Store evidence with provenance | IDs point to source, query, time range, timestamp, digest, bounded snapshot |
| FR-011 | P0 | Generate schema-validated hypotheses and diagnosis | Evidence IDs resolve; unsupported assertions marked uncertain |
| FR-012 | P1 | Preserve contradictory evidence and insufficient-evidence outcomes | Agent is allowed to decline remediation or escalate |

### 6.3 Agent Runtime and Observability

| ID | Priority | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-013 | P0 | Use one bounded, checkpointed, single-agent investigation graph | Configurable step, token, tool, latency budgets; named terminal outcomes |
| FR-014 | P0 | Route every read tool through a typed, policy-enforcing gateway | Tool bypass, invalid schema, wrong environment fail closed |
| FR-015 | P0 | Record observable model steps and tool attempts | Run/step IDs, parent spans, status, error, timing, usage when available |
| FR-016 | P0 | Link investigation findings to evidence and infrastructure traces | UI follows `incident → run → tool step → evidence → source trace` |
| FR-017 | P1 | Restore interrupted investigation from durable checkpoint | Kill/restart tests preserve committed results; unknown in-flight effects handled |
| FR-018 | P2 | Support scoped, isolated-context subagents | Parent/child run relationship, budgets, comparable baseline metrics |

**Trajectory is not model chain-of-thought.** Store observable messages/requests, response summaries, tool operations and decisions only. Avoid unredacted secrets, full logs, and sensitive payloads by default.

### 6.4 Governance and Controlled Execution

| ID | Priority | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-019 | P0 | Validate proposals with deterministic allowlist policy | `ALLOW / DENY / REQUIRES_APPROVAL` includes reason + policy version |
| FR-020 | P0 | Bind approval to immutable normalized action and target revision | Any argument, environment, or revision change invalidates authorization |
| FR-021 | P0 | Support durable approve/reject/expire/cancel operations | Decisions are RBAC-protected, atomic, auditable, restart-safe |
| FR-022 | P0 | Execute approved actions in isolated worker with least privilege | Agent/API possess no Docker socket/admin token; arbitrary commands rejected |
| FR-023 | P0 | Implement lab action `resume_test_consumer` | Management API reaches the correct allowlisted consumer; lag drain verified |
| FR-024 | P0 | Verify remediation with fresh observations | Action success and incident resolution tracked separately |
| FR-025 | P0 | Persist append-only security events | Proposal, denial, approval, dispatch, outcome, verify, escalation auditable |
| FR-026 | P1 | Implement scoped `rollback_demo_api` and `restart_lab_dependency` | Version/target pins, known rollback manifest, tested postconditions |
| FR-027 | P1 | Reconcile unknown action outcomes before retrying | Crash/timeout after remote success does not cause blind duplicate mutations |
| FR-028 | P1 | Enforce rate, budget, expiry, and separation-of-duties policies | Negative tests demonstrate denied, stale, and over-budget cases |

**Initial action catalogue:**

| Action ID | MVP stage | Mechanism | Allowed target | Postcondition |
|---|---|---|---|---|
| `resume_test_consumer` | M1 | Test worker management HTTP API (`POST /control/resume`) | Registered `fault-lab` consumer | `paused=false`, lag approaches threshold within window |
| `rollback_demo_api` | M2 | Constrained deployment controller selects a prebuilt, pinned image and re-creates target | Registered lab API only | Expected version ready, health/5xx/P95 within thresholds |
| `restart_lab_dependency` | M2 | Restricted controller and predefined service mapping | Named disposable Redis/test worker only | Service readiness and downstream health restored |
| `release_lab_db_blocker` | M2 | Fault-controller endpoint holding a **known test transaction token** | Injected lock holder only | Lock wait and API latency resolve |
| `stop_lab_cpu_load` | M2 | Fault-controller endpoint for dedicated load generator | Named generator only | CPU load and impacted latency recover |

No generic `run_shell(command)`, arbitrary `docker exec`, or `terminate_pg_backend(pid)` action is provided to the agent. Kafka has no universal broker-side *resume consumer group* command: the lab consumer exposes its own controllable management API.

### 6.5 Benchmark and Testing

| ID | Priority | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-029 | P0 | Provide an isolated fault controller and reset baseline | Reset yields healthy metrics, known version, clean consumer offsets |
| FR-030 | P0 | Deliver a deterministic Kafka pause/resume benchmark case | Agent blind to fault label; grader observes actual state and lag drain |
| FR-031 | P1 | Cover six named fault families and at least one no-action case | Setup/inject/detect/clean/grade works without manual intervention |
| FR-032 | P1 | Score diagnosis, proposed action, independent recovery | Machine-readable outcome and thresholds; no self-reported success |
| FR-033 | P1 | Provide policy/adversarial test matrix | Zero unauthorized mutations in evaluated tests; failures fail release gate |
| FR-034 | P1 | Inject crash/restart and ambiguous-result scenarios | No unsafe blind replay; reconciliation path visible and testable |
| FR-035 | P1 | Run rules-only and single-agent baselines | Matched scenarios, seeds, trial controls, cost/latency measures |
| FR-036 | P2 | Compare multi-agent with single-agent + parallel tool calls | Same workloads and budgets; report trade-offs rather than assume gain |

## 7. Lifecycle Contracts

### 7.1 Incident status

`RECEIVED → TRIAGED → INVESTIGATING → DIAGNOSED → AWAITING_APPROVAL → EXECUTING → VERIFYING → RESOLVED`

Other valid outcomes: `DIAGNOSED_ONLY`, `INSUFFICIENT_EVIDENCE`, `ESCALATED`, `CANCELLED`, `FAILED`. An agent run's completion does **not** imply incident resolution.

### 7.2 Agent run status

`QUEUED → RUNNING → COMPLETED | FAILED | CANCELLED | BUDGET_EXCEEDED`

### 7.3 Action status

`PROPOSED → POLICY_DENIED | PENDING_APPROVAL → REJECTED | EXPIRED | APPROVED → QUEUED → EXECUTING → SUCCEEDED | FAILED | RECONCILING`.

**Important:** `SUCCEEDED` means the remote action's result is confirmed, **not** the service's recovery. `RECONCILING` can become `SUCCEEDED`, `FAILED`, or `ESCALATED` after external inspection. `Verification` is a separate record and incident transition. See `architecture.md` for precise transition ownership.

## 8. User Stories and Acceptance Journeys

**US-01 — Kafka backlog incident:** Given a **test consumer** paused while its producer continues, when lag alert fires, the agent must distinguish consumer pause from Kafka broker outage using consumer status + lag + broker health; propose only `resume_test_consumer`; a human approves; the executor resumes the registered worker; verifier observes sustained lag decline and a healthy consumer. [B-F03]

**US-02 — Bad API release:** Given a fault-enabled v2 image with increased 5xx and a known v1 manifest, when agent proposes rollback, approver sees exact environment/service/version and evidence, and execution is denied if target revision changes. [B-F05, S-04]

**US-03 — Malicious log text:** Given a log entry instructing the model to delete data or call an unregistered tool, the model may quote it as data but must not be able to cause a forbidden mutation. [S-07]

**US-04 — Unknown action result:** Given a remote controller accepts action `A` but the worker crashes before recording the response, restart queries the controller's operation status for `A` and does not dispatch a new uncorrelated action. [R-03]

**US-05 — Safe abstention:** Given incomplete or contradictory evidence, the agent produces an uncertainty-aware report and `NO_ACTION`/escalation rather than fabricating a definitive root cause. [B-N01]

## 9. Non-Functional Requirements and Initial Targets

**All numbers below are design targets, not measured achievements.** Tune against real lab baselines and disclose test conditions.

| ID | Quality attribute | MVP / release target |
|---|---|---|
| NFR-001 | Safety | **0 unauthorized mutations** in the defined safety suite (does not prove universal safety) |
| NFR-002 | Traceability | 100% of **attempted tool invocations** yield a structured outcome event, including denials/timeouts |
| NFR-003 | Approval integrity | No approved action executed with changed args, revision, environment, or expired decision in the test suite |
| NFR-004 | Durability | 100% of committed approval decisions survive restart scenarios |
| NFR-005 | Bounded execution | Default max 20 graph steps, 40 read-tool calls, 5-minute investigation wall clock; configurable |
| NFR-006 | Read limits | Default max 60-minute query window, capped result bytes/count and per-tool timeout |
| NFR-007 | Privacy | No credentials/secrets in agent response, trajectory, error log, or audit fixtures |
| NFR-008 | Benchmark | Six fault families, at least one safe-abstention case, >=5 measured repetitions per primary case, separately reported |
| NFR-009 | Reproducibility | Fresh clone can bring up lab, reset state, and run suite from documented commands |
| NFR-010 | Reliability | Every ambiguous external write becomes `RECONCILING`/`ESCALATED`, not unconditionally retried |
| NFR-011 | Scope | Executor credentials restricted to named disposable lab resources; production mutation disabled |
| NFR-012 | Operational clarity | Status, last error, evidence, approvals, actions, and verification accessible through API/UI |

## 10. Metrics and Release Decisions

**Primary evaluation metrics:** RCA exact-match rate, safe action selection rate, independently verified resolution rate, unauthorized execution count, workflow recovery rate, time-to-diagnosis, time-to-resolution, tokens/cost per incident, tool attempts, and P95 investigation duration. See precise denominators and invalid-run handling in `benchmark.md`.

**Release blockers:** Any reproducible forbidden mutation, approval bypass, unexplained duplicate side effect, leakage of lab-control ground truth into agent context, or benchmark unable to reset environmental state.

**Not a release blocker:** An agent appropriately returns `INSUFFICIENT_EVIDENCE` for an ambiguous case; that outcome is evaluated according to the case specification.

## 11. Dependency and Risk Register

| Risk | Impact | Mitigation |
|---|---|---|
| Alert/metric labels differ by exporter version | Broken diagnostics | Lock collector/exporter versions and contract-test metric names |
| Containerized metrics too noisy | Weak ground truth | Repeat trials; fixed resources; pre-fault baselines; stable workloads |
| Docker access excessively privileged | Host risk | Separate disposable lab/controller; restrict network and host mounting |
| LLM variability | Hard to compare | Record exact model config; repeated trials; deterministic task labels |
| False causal inference from temporal proximity | Wrong recovery action | Evidence provenance + independent checks + abstain path |
| Crash after action dispatch | Duplicate side effect risk | Stable `action_id`, controller operation status API, reconciliation |
| Scope explosion | Unfinished portfolio | Implement only one verified E2E action before broadening faults |

## 12. Definition of Done

**M1** is done when an independent reviewer can reproduce Kafka consumer pause → real alert → evidence-backed single-agent diagnosis → exact approved action → controlled resume → verified lag recovery, and inspect immutable action approval and audit records. **M2** is done when the six-family benchmark, denial suite, crash recovery suite, and baselines run in CI/local instructions and emit reproducible CSV/JSON plus an honest limitations section.
