# ADR-028: A self-describing submit_diagnosis schema

- **Status:** Accepted
- **Relates to:** ADR-008, ADR-025, ADR-027;
  `docs/proposals/2026-11-14-submit-diagnosis-reliability.md` (options A-C)

## Context

In the 2026-11-13 deepseek-flash run, 94 of 179 `submit_diagnosis` calls
were rejected before their content could be judged. Besides 57 empty
arguments (dropped by the provider), the model put top-level fields inside
`diagnosis` (26) and sent `diagnosis` as a JSON-encoded string (29). The tool
had a one-line description, and only `confidence_level` was described in its
schema.

## Decision

### A. Every field says what it is and where it lives

Every field of `DiagnosisReport`, `Diagnosis` and `ActionProposalDraft` has a
description, and those descriptions are what the model reads in the tool
schema. They state that `proposed_action` and `needs_human_review` are
top-level fields, not part of `diagnosis`. They say that `diagnosis` and
`proposed_action` are JSON objects, never JSON-encoded strings. They also say
that `affected_component` is the failing service, not the object an action
would change. The tool description gives the three top-level fields and one
complete example of the shape, with placeholder values only. Prompt version
`investigation-v10`.

## Consequences

- Validation is unchanged: descriptions only guide the model.
- The descriptions appear wherever the schema is shown (e.g. API docs).
