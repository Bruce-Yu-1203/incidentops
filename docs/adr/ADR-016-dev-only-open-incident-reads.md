# ADR-016: Incident read API is open only in the dev profile until P3

- **Status:** Accepted (temporary). P3 added session authentication: every
  mutating human endpoint now requires a session with an explicit role, and
  authenticated reads use the viewer role. The anonymous dev-profile read
  exception remains until P4 security hardening removes it.

## Context

P1 needs an incident list/detail view, but human session authentication
(ADR-011) is scheduled with approvals in P3.

## Decision

- `GET /api/v1/incidents` and `GET /api/v1/incidents/{id}` are served without
  authentication **only** when `INCIDENTOPS_PROFILE=dev`; every other profile
  returns 401. They expose no mutation.
- The API listens on `127.0.0.1` in local compose files.
- All mutating endpoints require a service identity from day one: the
  Alertmanager webhook requires its own bearer token and fails closed (503)
  when no token is configured.

## Consequences

P3 must replace the dev-profile check with the `viewer` role dependency and
remove this exception.
