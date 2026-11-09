# ADR-012: Evidence retention window for local demos

- **Status:** Accepted (Phase 0 default)
- **Resolves:** architecture.md §17 question 5

## Decision

- Evidence snapshots are redacted before storage and capped at 16 KiB each.
- Local `dev` and `demo` profiles retain evidence, trajectory payloads and
  run records for **7 days**; the `benchmark` profile retains them for
  **30 days** so reports can be re-graded. Audit events and approval decisions
  are not purged by this policy.
- Raw telemetry stays in Prometheus/Loki/Tempo with their own retention; the
  control plane stores references and digests only.

## Consequences

A purge job is required before P6; until then the retention values are
configuration defaults.
