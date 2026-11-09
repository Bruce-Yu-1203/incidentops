# ADR-018: Defer container CPU/memory metrics (cAdvisor) to P5

- **Status:** Accepted
- **Relates to:** roadmap P1 task "Docker CPU/memory", tech-stack §5.3

## Context

cAdvisor and similar collectors need the Docker socket or host cgroup mounts.
The constitution forbids mounting the Docker socket in this repository's compose
files, and P1's gate faults (Kafka pause, DB lock) do not need container metrics.

## Decision

- P1 ships without container CPU/memory metrics.
- P5 (CPU saturation, memory leak and deployment faults) adds them through an
  isolated, read-only collector on a disposable lab host, documented with its
  own threat model, before B-F04 and B-F06 are implemented.

## Consequences

Until then, the agent cannot use container resource evidence; tests guard that
no compose service mounts `docker.sock`.
