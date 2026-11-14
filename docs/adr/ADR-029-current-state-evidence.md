# ADR-029: Root-cause component, restart precondition and current-state telemetry

- **Status:** Accepted
- **Relates to:** ADR-027, ADR-028; policy `p7`; prompt `investigation-v11`;
  `docs/proposals/2026-11-14-r2-follow-ups.md` (items 1, 2, 3b, 3c)

## Context

The 2026-11-14 r2 run (`docs/benchmark-results/2026-11-14-B1-deepseek-flash-v2.1-r2/`)
left nine non-PASS trials. Two named the alerting service instead of the
failing dependency (B-F12 r2, r4). One executed a database restart in the
no-fault case (B-N01 r1), based on readings from the previous trial's
outage, which was still inside the lookback window. The same window effect
caused the wrong releases of 2026-11-13 (ADR-027).

## Decision

### 1. `affected_component` is the root-cause component

The field description and a prompt rule say that `affected_component` is
the component where the root cause lives. When the alerting service fails
because one of its dependencies fails, the diagnosis names the dependency,
even for the incident opened on the alerting service. Every incident caused
by one fault gets the same `affected_component`. Grading is unchanged.
Prompt `investigation-v11`.
