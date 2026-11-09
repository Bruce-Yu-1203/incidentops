# ADR-019: Anthropic-compatible model endpoints

- **Status:** Accepted
- **Amends:** ADR-008

## Context

The owner wants to run the investigation agent against DeepSeek before using
Anthropic directly. DeepSeek and some gateways expose an Anthropic-compatible
Messages API at a different base URL.

## Decision

- `INCIDENTOPS_ANTHROPIC_BASE_URL` optionally overrides the Anthropic SDK base
  URL; when empty, the SDK default (`api.anthropic.com`) is used.
- The URL must be `https://` (plain `http://` only for loopback), because the
  API key is sent to it.
- The configured `INCIDENTOPS_MODEL_ID` (for example `deepseek-chat`) is recorded
  on every run as before, so benchmark results always name the provider model.
- Compatibility of tool use and structured output with a third-party endpoint is
  not assumed: runs that return invalid diagnoses fail validation and are
  reported as failures, never repaired silently.

## Consequences

Switching providers is configuration only. Results from different endpoints
must be reported separately in benchmarks.
