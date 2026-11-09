# ADR-013: `VERIFICATION_FAILED` is a verification result, not an incident state

- **Status:** Accepted
- **Resolves:** inconsistency between architecture.md §10/§13 and the incident
  state machine in §5.1

## Context

Architecture §10 says a completed action without metric recovery is
`VERIFICATION_FAILED` "at the incident layer", but the incident state machine
in §5.1 and requirements §7.1 have no such state.

## Decision

- `VerificationResult.outcome` is one of `PASSED`, `VERIFICATION_FAILED` or
  `UNKNOWN`.
- The incident state machine is kept exactly as specified. `VERIFYING` moves to
  `RESOLVED` only on `PASSED`; both `VERIFICATION_FAILED` and `UNKNOWN` move the
  incident to `ESCALATED` with that outcome recorded as the reason code.
- The UI distinguishes these escalation reasons from `RECONCILING` as required
  by architecture §13.

## Consequences

The documents stay consistent without adding a state; the escalation reason is
queryable.
