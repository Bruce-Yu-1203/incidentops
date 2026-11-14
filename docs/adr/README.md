# Architecture Decision Records

ADR-001 to ADR-007 are summarized in [architecture.md §16](../architecture.md).
The records below resolve the open questions of architecture.md §17 and other
gaps found while implementing Phase 0. Each one is a reversible default: change
it with a new ADR that supersedes the old one.

| ADR | Title | Status |
|---|---|---|
| [ADR-008](ADR-008-model-provider.md) | Provider-neutral model adapter with a scripted test double | Accepted |
| [ADR-009](ADR-009-kafka-lag-source.md) | Kafka lag source and consumer control contract | Accepted |
| [ADR-010](ADR-010-controller-operation-ledger.md) | Controller operation ledger and generation pinning | Accepted |
| [ADR-011](ADR-011-ui-authentication.md) | Local UI authentication and identity seeding | Accepted |
| [ADR-012](ADR-012-evidence-retention.md) | Evidence retention window for local demos | Accepted |
| [ADR-013](ADR-013-verification-failed-state.md) | `VERIFICATION_FAILED` is a verification result, not an incident state | Accepted |
| [ADR-014](ADR-014-repository-layout.md) | Repository layout and import boundaries | Accepted |
| [ADR-015](ADR-015-action-cancellation.md) | Action cancellation and pre-dispatch revalidation failures | Accepted |
| [ADR-016](ADR-016-dev-only-open-incident-reads.md) | Incident read API is open only in the dev profile until P3 | Superseded (P4) |
| [ADR-017](ADR-017-incident-dedup-key.md) | Incident dedup key is environment + service | Accepted |
| [ADR-018](ADR-018-container-metrics-deferred.md) | Defer container CPU/memory metrics (cAdvisor) to P5 | Accepted |
| [ADR-019](ADR-019-anthropic-compatible-endpoints.md) | Anthropic-compatible model endpoints | Accepted |
| [ADR-020](ADR-020-p5-fault-simulation.md) | How the P5 fault families are simulated in the lab | Accepted |
| [ADR-021](ADR-021-human-disposition.md) | Humans close handed-over incidents with a disposition, not a state | Accepted |
| [ADR-022](ADR-022-incident-chat.md) | A read-only incident chat answered by the agent worker | Superseded in part by ADR-023 |
| [ADR-023](ADR-023-chat-agent.md) | A separate chat agent with read tools, and human-requested re-investigation | Accepted |
| [ADR-024](ADR-024-root-cause-coverage.md) | Root-cause/action consistency and expanded root-cause coverage | Accepted |
| [ADR-025](ADR-025-tool-descriptions-prompt-confidence.md) | Tool descriptions, investigation method and confidence rules | Accepted |
| [ADR-026](ADR-026-benchmark-grading-2-1.md) | Benchmark grading 2.1: stricter gates and secondary metrics | Accepted |
| [ADR-027](ADR-027-evidence-preconditions-and-abstention.md) | Evidence preconditions for database releases, and no-fault abstention | Accepted |
