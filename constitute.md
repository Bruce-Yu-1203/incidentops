# IncidentOps Project Constitution

This document is binding for every contributor, human or AI, working in this
repository. If a task conflicts with a rule below, stop and raise the conflict
instead of silently working around it. The design documents in [`docs/`](docs/)
describe *what* to build; this constitution describes *how* we are allowed to
build it.

## Article I — Non-negotiable working rules

1. **Commit after every modification.** Every completed change MUST be recorded
   as its own git commit following the
   [Conventional Commits](https://www.conventionalcommits.org/) standard:
   `<type>(<optional scope>): <imperative summary>`. Allowed types: `feat`,
   `fix`, `docs`, `test`, `refactor`, `perf`, `build`, `ci`, `chore`, `style`,
   `revert`. Breaking changes use `!` or a `BREAKING CHANGE:` footer. One
   logical change per commit; never batch unrelated work.
2. **Tests accompany every change.** Every modification MUST create or update
   the related tests. Before work is handed to the user, the full validation
   suite (`make check`: lint, format check, type check, tests) MUST pass. A
   change whose tests are skipped, xfailed without reason, or not run is not
   done. If a validation cannot be run locally (e.g. Docker unavailable), say so
   explicitly and do not claim it passed.
3. **English only in code.** All code comments, docstrings, identifiers, log
   messages, commit messages and repository documentation are written in
   English.

## Article II — Safety boundary (from `docs/architecture.md` §1–§2)

1. **AI output is untrusted intent.** The investigation agent may only read.
   Mutations are expressed as a typed `ActionProposal` artifact and are never
   executed by the agent process.
2. **No agent write credentials.** The agent/investigation worker process MUST
   NOT receive Docker socket access, fault-controller credentials, executor
   tokens, write-enabled lab database credentials, or any shell tool. Code
   review rejects any change that makes such a credential reachable from the
   agent's configuration.
3. **Deterministic authorization.** Policy decisions are made by deterministic
   code (`ALLOW` / `DENY` / `REQUIRES_APPROVAL`). Policy errors, unknown
   operations and unavailable policy evaluation **fail closed**.
4. **Exact approvals.** A human approves a canonical, hashed, immutable action
   envelope bound to target revision and TTL. The executor re-validates hash,
   TTL, policy and target revision immediately before dispatch.
5. **Allowlist only.** Never accept model-generated shell commands, SQL, URLs,
   IP addresses, Docker resource names or image tags. Targets resolve through
   the versioned service catalog.
6. **Lab only.** Remediation and fault injection run only against the
   disposable Fault Lab. No production credentials exist in this repository.
7. **Separated identities.** API, agent, executor, verifier and fault injector
   (benchmark controller) use distinct credentials. The fault injector's
   capabilities and benchmark ground truth are never visible to the agent.

## Article III — Reliability and state discipline

1. Incident, investigation run, action and approval have **separate** state
   machines. Every transition goes through the domain state machine and is
   covered by a unit test; illegal transitions raise errors.
2. Persisted transitions use compare-and-swap on `(state, version)` in the same
   transaction as their audit and outbox writes. A zero-row update is a
   conflict, not a silent retry.
3. Never describe the system as exactly-once. Leased jobs are at-least-once;
   ambiguous external outcomes go to `RECONCILING` or `ESCALATED` and are never
   blindly replayed.
4. "Agent run completed", "action succeeded" and "incident resolved" are three
   different facts. Only the independent verifier can drive `RESOLVED`.

## Article IV — Evidence and benchmark integrity

1. **No fabricated numbers.** Never write accuracy, latency, cost, safety or
   recovery figures into README, docs, reports or commit messages unless they
   come from a recorded, reproducible run. Targets are labelled as targets.
2. Benchmark ground truth (`expected` blocks, injection credentials) lives only
   in benchmark-controller code paths and must never reach prompts, tool
   descriptions, tool results or agent-visible telemetry labels.
3. Invalid trials are counted and reported, never silently dropped.
4. Never use LLM self-assessment or an HTTP 200 as proof of recovery.

## Article V — Scope discipline (from `docs/roadmap.md` §14)

1. Follow the roadmap order (P0 → P7). Advance a phase only when its gate
   passes. Reduce optional features rather than weakening safety tests.
2. Do not build a monitoring system, a second job broker (no Kafka in the
   control plane), multi-agent orchestration, self-improvement, Kubernetes or
   OPA before the roadmap calls for them.
3. Add a dependency only when it solves a stated need; pin it in a lockfile.
4. Open questions are not blockers: pick a reasonable, reversible default and
   record it as an ADR under `docs/adr/`.

## Article VI — Engineering standards

1. Python ≥ 3.12, typed code, `ruff` for lint/format and `mypy --strict` for the
   `incidentops` package. TypeScript in strict mode for the web app.
2. Dependencies are locked (`uv.lock`, `pnpm-lock.yaml`); container images use
   explicit version tags, and digests where practical.
3. Secrets never enter git, logs, traces, audit records or fixtures. Only
   `.env.example` with placeholder values is committed.
4. Request handlers stay thin: authenticate → validate → transaction → outbox.
   Long-running or external work happens in workers.
5. Schema changes happen only through Alembic migrations, and migrations are
   tested against a real PostgreSQL in CI.
6. Prefer small, reviewable changes. Documentation that drifts from the code is
   a bug: update `docs/` in the same change when behavior diverges from it.

## Article VII — Amendments

This constitution changes only through a `docs(constitution): ...` commit that
explains the reason for the change.
