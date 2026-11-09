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
