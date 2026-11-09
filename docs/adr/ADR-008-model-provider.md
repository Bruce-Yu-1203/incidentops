# ADR-008: Provider-neutral model adapter with a scripted test double

- **Status:** Accepted (Phase 0 default)
- **Resolves:** architecture.md §17 question 1

## Context

The investigation agent needs reliable tool calling. The exact provider and
model are not chosen yet, and CI must not spend money on model calls.

## Decision

- All model access goes through a `ModelAdapter` protocol. No module outside
  `incidentops/agent/` imports a provider SDK.
- The first real adapter targets the Anthropic Messages API because it offers
  native tool use and structured output. The model ID is configuration
  (`INCIDENTOPS_MODEL_ID`) with no hard-coded default, and is recorded on every
  investigation run and benchmark trial.
- A deterministic `ScriptedModelAdapter` replays recorded responses for unit,
  contract and security tests. CI uses only the scripted adapter.

## Consequences

Benchmark metadata must capture the model ID reported at runtime. Switching
providers requires only a new adapter and re-running the benchmark.
