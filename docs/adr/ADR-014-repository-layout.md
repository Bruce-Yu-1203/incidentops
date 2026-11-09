# ADR-014: Repository layout and import boundaries

- **Status:** Accepted

## Decision

- `incidentops/` is the control-plane library (domain, persistence, API,
  workers). Process entry points live under `incidentops/` modules and are
  started with different credentials; `apps/web/` holds the Next.js UI.
- `fault-lab/` is a separate uv workspace member (`incidentops-fault-lab`,
  package `fault_lab`) with its own dependencies and container images.
- The control-plane package must never import `fault_lab`, and no control-plane
  module except the executor may reference executor credentials. Both rules
  are enforced by tests in `tests/security/`.

## Consequences

Fault-lab services can be rebuilt or replaced without touching the control
plane, and the agent process cannot reach fault-injection code by import.
