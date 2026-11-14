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

### B. Closed vocabularies instead of free-form IDs

The tool schema replaces free-form IDs with the values that are possible,
built from existing configuration:

| Field | Allowed values from |
|---|---|
| `diagnosis.root_cause_id` | the root-cause taxonomy (incl. `insufficient_evidence`); a test keeps it equal to the policy's `root_cause_actions` keys |
| `diagnosis.affected_component` | registered service IDs (catalog) |
| `proposed_action.action_type` | policy rule action types |
| `proposed_action.target_id` | registered services that allow any action |
| `proposed_action.arguments` | a closed object: every argument name any policy rule allows, each with its allowed values; the description lists each action's required arguments in the `one_of` form |

The schema names what is *possible*, never what is right for a case, and
no scenario data reaches it (tested). The schema grows from about 2.8 KB to
about 5.8 KB. JSON Schema cannot tie the required arguments to
`action_type` in a form that models follow reliably, so the per-action
contract is in the arguments description. Policy still enforces it exactly.

### C. Lift one known misplacement

When `proposed_action` or `needs_human_review` appears inside `diagnosis`,
the harness moves it up to the top level before validation. It does that
only if the top level does not already have the field, or has the same value
(then the nested copy is dropped). Conflicting copies stay where they are,
so validation rejects the submission as before. Nothing is invented and no
value changes. Each lift is recorded in the diagnosis step (`lifted_fields`)
and counted in `investigation_runs.field_lifts` (migration 0011). The count
is also carried into trial records, the CSV and the report's Usage section,
alongside `argument_coercions`.

## Consequences

- Validation and policy are unchanged. Descriptions and enums only guide the
  model, and the lift is a counted, unambiguous move of an existing value.
- The descriptions appear wherever the schema is shown (e.g. API docs).
