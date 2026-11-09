# ADR-017: Incident dedup key is environment + service

- **Status:** Accepted
- **Relates to:** FR-002, FR-005, roadmap P1 gate

## Context

The roadmap requires "one active incident per configured key". A single fault
often trips several rules for the same service (a database slowdown raises both
API P95 latency and the 5xx ratio). Keying by rule would open one incident per
symptom and split the evidence.

## Decision

- The dedup key is `<environment>:<service_id>`. Every firing alert for a
  service with an active incident attaches to that incident as a retained
  alert event, whatever rule produced it.
- A new incident for the same service opens only after the previous one reaches
  a terminal state.
- Alert rules must carry `service_id`, `environment` and `severity` labels;
  labels describe the affected domain, never the hidden root cause.

## Consequences

Two unrelated faults on the same service in overlapping windows share one
incident. The investigation sees all alert events and may report contradicting
evidence; revisit if the benchmark shows this hurts diagnosis.
