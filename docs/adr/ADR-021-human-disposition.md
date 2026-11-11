# ADR-021: Humans close handed-over incidents with a disposition, not a state

- **Status:** Accepted
- **Relates to:** ADR-017, requirements §state machine, web console redesign

## Context

`ESCALATED`, `FAILED` and `DIAGNOSED_ONLY` are terminal states: the system has
stopped acting and handed the incident to a person. Before this decision the
console kept showing them as open work, and there was no way for a person to
say "I dealt with it" or "it was a false alarm". The obvious fix, a new
`CLOSED` state reachable from `ESCALATED`, would make `ESCALATED` non-terminal.
The partial unique index `uq_incidents_active_dedup_key` (ADR-017) covers only
non-terminal states, so a non-terminal `ESCALATED` would block a fresh incident
for the same alert while it waits for a person.

## Decision

- The incident state machine is unchanged. A handed-over incident carries an
  optional human layer: `owner_username`/`assigned_at` and
  `disposition`/`disposition_note`/`disposed_by`/`disposed_at`.
- Disposition values: `FIXED_MANUALLY`, `RECOVERED_ON_ITS_OWN`, `FALSE_ALARM`,
  `WONT_FIX`. A database check constraint allows a disposition only on a
  handover state and only with an actor and time.
- An incident "needs a person" when it is in a handover state and has no
  disposition. The inbox uses this; history keeps everything.
- Assign and close are operator actions (operator, approver, admin). Both bump
  the incident version with compare-and-swap and write an audit event in the
  same transaction. Closing never runs an action.
- A `resolved` alert delivery with no active incident is stored on the newest
  handed-over incident for the same dedup key that still waits for a person.
  It never changes state; the console shows it as "alert cleared N min ago".
- "Re-check now" re-evaluates the incident's verification metric checks once,
  read-only, from Prometheus. It uses no model and no executor, is audited, and
  only reports; a person still decides.

## Consequences

Dedup semantics stay as they were. "Resolved" keeps meaning "verified by the
system"; a human closure is reported separately in history, reports and
metrics, so automation results are not inflated by manual fixes.
