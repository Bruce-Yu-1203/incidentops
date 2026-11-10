# Deployment, cost and data handling

## Supported deployment

IncidentOps is a **lab prototype**. The supported deployment is the local
Docker Compose environment started by `make demo-up` on one machine. There is
no production profile: `Settings` rejects `INCIDENTOPS_PROFILE=production`,
policy configuration rejects production remediation rules, and no production
credentials exist anywhere in the repository.

### Why production automation stays disabled

- The safety evidence comes from a small, simulated fault set on one host
  (see the benchmark limitations). It does not show that the runtime is safe
  against real production failure modes.
- The executor's adapters target lab-specific management APIs. Real systems
  would need their own adapters, revision semantics and rollback guarantees.
- Local username/password sessions are not an enterprise identity provider.
- A wrong remediation in production has real cost; the project goal is to make
  AI-assisted response traceable and testable first.

Enabling a staging integration would require a separate, read-only adapter
first, then a distinct policy and identity boundary before any write
(roadmap P7).

## Resource footprint (local)

Measured on the development Mac (Docker with 10 CPUs / 8 GB available):
the full stack (control database, lab, telemetry, five control-plane processes
and the web UI) runs comfortably within that allocation. As a guide, give
Docker at least 4 CPUs and 6 GB. Prometheus keeps 7 days of data; Loki and
Tempo keep 7 days (168 h) of logs and traces.

## Cost

- **Infrastructure:** local only; no cloud services are required.
- **Model usage:** the default rules baseline costs nothing. With an LLM, each
  investigation makes at most 20 model steps and 40 tool calls (configurable
  budgets). Token counts per run are recorded on the investigation run and in
  benchmark reports; the project does not estimate prices it has not observed.
- **CI:** every pull request runs lint, types and all automated suites on
  GitHub-hosted runners with no model calls and no secrets. The live benchmark
  workflow is manual (`workflow_dispatch`) and uses the rules baseline.

## Data handling assumptions

- Telemetry stays in Prometheus, Loki and Tempo. IncidentOps stores evidence
  references, digests and redacted snapshots capped at 16 KiB each (ADR-012).
- Secrets and personal data (passwords, tokens, keys, emails, credentials in
  URLs) are redacted before evidence, trajectory or audit storage; this is
  pattern-based and best-effort, so raw payloads are also size-bounded.
- Hidden model reasoning is never stored; only observable inputs, outputs and
  tool calls are recorded.
- Model prompts contain the incident, its alerts, the service catalog and the
  root-cause label list; never benchmark ground truth, injection details or
  credentials. When an external model provider is configured, those prompts
  and redacted tool results are sent to that provider.
- Passwords are stored as scrypt hashes and session tokens as SHA-256 digests.
- Audit events are append-only for every application role.

## Maintenance

- Pin changes: Python dependencies via `uv.lock`, web via `pnpm-lock.yaml`,
  lab images by digest, lab Python dependencies via the hashed
  `fault-lab/requirements.lock` (`make lab-lock`).
- Schema changes go through Alembic migrations and must update
  `persistence/privileges.py` when tables change; a test compares the live
  grants with that matrix.
- Policy and catalog changes are versioned (`policy_version`,
  `catalog_version`); a policy change invalidates pending approvals at
  dispatch time.
- Re-run `make security-suite`, `make recovery-suite` and a benchmark after
  changes to tools, policy, executor or verifier.
