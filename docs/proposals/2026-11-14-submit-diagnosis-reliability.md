# Proposal: make `submit_diagnosis` reliable across models

- **Date:** 2026-11-14
- **Status:** Proposed, awaiting a decision
- **Evidence:** `docs/benchmark-results/2026-11-13-B1-deepseek-flash-v2.1/`
  (70 trials, 91 investigations, 179 `submit_diagnosis` calls)

## Problem

In the DeepSeek (deepseek-flash) run, 94 of 179 diagnosis submissions were
rejected before validation could look at their content. Four trials failed
only because every submission was rejected until the three repair attempts
ran out (B-F01 r4, B-F05 r1, B-F10 r4, B-F11 r5). In each of them the
investigation itself had found the cause.

| Rejected shape | Count | Example (keys only) | Whose problem |
|---|---|---|---|
| Empty arguments | 57 | `{}` | Provider: DeepSeek's Anthropic-compatible endpoint returns `"input": {}` |
| Field at the wrong level | 26 | `{"diagnosis": {..., "needs_human_review": true}, "proposed_action": ...}` | Model: the schema is easy to misread |
| `diagnosis` as a string, invalid JSON | 9 | `{"diagnosis": "{\"root_cause_id\": ...", ...}` | Model |
| `diagnosis` as a string, valid JSON | 20 | same, but parseable | Model; already parsed automatically since `3c1a0bc` |

Measured facts about the empty arguments:

- They are not caused by size. Empty and accepted submissions have the same
  output length (mean 2,852 vs 3,035 output tokens).
- They happen on any attempt: 25/91 first attempts, 15/55 second, 11/21
  third and 6/12 fourth. A retry helps, but it can be empty again.
- Read-tool calls in the same conversations always arrive intact. The raw
  HTTP response contains `"input": {}`, so our client is not losing anything.

The most likely cause is that DeepSeek's compatibility layer fails to parse
the long arguments the model generated (e.g. an unescaped character in
`summary`) and drops them. We cannot confirm this through the
Anthropic-compatible endpoint, because it never returns the raw text.

## Why the schema invites mistakes

The tool the model sees has a one-line description ("Submit the final,
evidence-linked diagnosis. Call exactly once.") and a 2.8 KB schema with:

- three levels of nesting, where `needs_human_review` sits next to
  `diagnosis` rather than inside it;
- no description on any field except `confidence_level`;
- `root_cause_id` as a free string with a regex. The allowed values are
  listed only in the system prompt;
- two dictionaries keyed by evidence ID (`evidence_notes`,
  `proposed_action.arguments`) declared with `patternProperties`, which many
  models handle badly;
- `proposed_action` as `anyOf [object, null]`.

## Options

Each option is independent. Effort is for one person, including tests,
`make check` and an ADR where noted.

### A. Describe every field (cheap, recommended)

Add a `description` to every field of `DiagnosisReport`, `Diagnosis` and
`ActionProposalDraft`. These descriptions are what the model reads in the
tool schema. Say explicitly where each field belongs, e.g.
"`needs_human_review` (top level, next to `diagnosis`, not inside it)".
Expand the tool description to two or three sentences that give the
top-level shape.

- **Effort:** about 1 hour.
- **Risk:** none for validation. It changes the model's input, so prompt
  results may shift slightly. It needs a prompt-version bump.
- **Expected effect:** fewer wrong-level submissions (26 here). It does not
  touch empty arguments.

### B. Put the allowed values in the schema (cheap, recommended)

Turn `root_cause_id` into an `enum` of the root-cause taxonomy. Also consider
`affected_component` and `target_id` as `enum`s of the registered service
IDs; B-F13 r1 named `lab-api-config` instead of `lab-api`. Validation still
re-checks everything, as today.

- **Effort:** about 1-2 hours.
- **Risk:** low. The taxonomy and catalog already exist, and the enum is
  built from them, so it cannot drift. The schema grows by a few hundred
  bytes.
- **Expected effect:** no invented or mistyped IDs. It may help B-F13-style
  component misses.

### C. Tolerate one more known misplacement (cheap, needs your call)

Like the JSON-string parsing added in `3c1a0bc`: if `needs_human_review` (or
`proposed_action`) appears inside `diagnosis` and not at the top level, move
it up before validation, and count it in `argument_coercions`. Nothing else
changes. If the field is in both places, or the values conflict, the
submission is still rejected.

- **Effort:** about 1 hour.
- **Risk:** low. Nothing is invented and no value is changed. But it is a
  leniency, so the run report should keep counting it, as it does for JSON
  strings.
- **Expected effect:** about 26 fewer rejections in a run like this one.
  Fewer repair turns also means fewer tokens.

### D. Use strict tool schemas on the Claude path (medium)

When the model is Claude through the official API, send the tools with
`strict: true`. The API then guarantees that the arguments match the schema:
no empty, mis-nested or string-encoded objects. This needs a schema that
strict mode accepts. `patternProperties` and some regex patterns may not be
supported and would have to change, e.g. `evidence_notes` as a list of
`{evidence_id, note}` instead of a dictionary. That changes the stored report
format, so it needs a migration plan and an ADR.

- **Effort:** half a day to a day.
- **Risk:** medium (it touches the report format). It applies only to models
  that support strict tools.
- **Expected effect:** the rejections in the table above should essentially
  disappear on Claude. It does nothing for DeepSeek.

### E. Talk to DeepSeek through its native API (medium, diagnostic)

DeepSeek's native OpenAI-format API returns the tool arguments as the raw
string the model generated. With a small OpenAI-format adapter we could see
the malformed JSON behind each empty submission. We could then repair the
safe, mechanical cases (as we do for valid JSON strings) or at least report
exactly what went wrong.

- **Effort:** about a day (a new adapter next to the Anthropic one, with
  thinking, usage and error handling, and tests).
- **Risk:** medium. It is a second adapter to maintain.
- **Expected effect:** it would explain, and probably cut, the 57 empty
  submissions. It only matters if we keep using DeepSeek.

### F. Split the submission into two smaller tools (larger, not recommended now)

`submit_diagnosis` (diagnosis only) followed by an optional `propose_action`.
Smaller calls are easier for weak models. But it changes the graph,
validation, prompt and grading inputs, and the measurements above do not
show size as the cause of empty arguments.

- **Effort:** one to two days. Needs an ADR.

## Recommendation

1. **Now:** A + B + C. Together these are about half a day. They are cheap,
   model-independent and should remove most of the 26 wrong-level and
   invalid-ID rejections. Then run a 14x1 smoke pass to measure the effect
   (about $0.17 on deepseek-flash, or about $0.3 on Claude Haiku 5.5).
2. **If we move to Claude:** D, which removes the remaining structural errors
   on that path.
3. **If we stay on DeepSeek:** E, to understand and reduce the empty
   arguments.
4. **Not now:** F.

The 9 invalid-JSON strings stay rejected in every option; guessing at broken
JSON could change the meaning of a diagnosis. The repair attempts (3 for
model runs) remain the safety net.

## Decisions needed

- A, B, C: go or no-go. C is a leniency, like the JSON-string parsing.
- Which model the next runs use (DeepSeek, Claude Haiku 5.5, or both). This
  decides between D and E.
