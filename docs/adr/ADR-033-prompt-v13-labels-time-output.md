# ADR-033: Prompt investigation-v13: evidence labels, time anchor, short output

- **Status:** Accepted (Amy approved P6, P7 and P9 on 2026-11-16)
- **Relates to:** ADR-028 (submit schema), ADR-029 (current-state evidence),
  ADR-031 (v12); `docs/proposals/2026-11-16-prompt-after-v12.md` (items 1-3)

## Context

In r2 (prompt v10) 30% of `submit_diagnosis` calls arrived with empty
arguments, most likely because the provider failed to parse long argument
text; 5 submissions were rejected for miscopied 32-hex evidence IDs; and the
no-fault control named a fault from readings left by an earlier, resolved
problem. v12 was never run; v13 builds on it so each version stays honest.

## Decision

1. **Evidence labels (P6).** Each tool result shows its evidence as a per-run
   label `E1`, `E2`, ... in the `evidence_id` field, in the order the run
   collected it. The model cites labels in `supporting_evidence_ids`,
   `contradicting_evidence_ids`, `evidence_notes` keys and
   `proposed_action.evidence_ids`; the tool schema's patterns ask for labels.
   Before validation the harness maps labels to the run's real evidence IDs
   (`resolve_evidence_labels`); real IDs still pass through. A label the run
   never issued is rejected with the same `unresolved evidence ids` error as an
   unknown ID. Stored diagnoses, policy evaluation, audit and the console keep
   the real IDs. The chat agent is unchanged.
2. **Time anchor (P7).** The incident in the first message carries
   `investigation_started_at` and `alerts_at_investigation_start` (per alert
   fingerprint: name, `starts_at`, `still_firing` from its latest delivery). A
   `<time_anchor>` block says a reading that ended more than 5 minutes before
   the alert started is history, not current evidence. No tool window is cut.
3. **Short output (P9).** An `<output_style>` block: summary at most 3
   sentences, one sentence per evidence note, cite the 3-6 labels the
   conclusion rests on, no line breaks, double quotes or backslashes in
   strings, no pasted PromQL or log text, no restating earlier tool results.
   Prompt only: the schema limits are unchanged, so a long answer is not
   rejected.
4. `PROMPT_VERSION = "investigation-v13"`. The rules the tool schema already
   carries are kept until Amy decides (proposal item 4). Root-cause definitions
   (P8) are a draft for review, not part of v13.

## Consequences

- Expected effect, to be measured: fewer empty and rejected submissions and
  fewer output tokens per submission (r2: 30%, about 2,900), and no fault named
  from earlier readings in the no-fault control (r2 B-N01: 2/5).
- The 5-minute rule is generic incident practice (diagnose from the current
  state) and uses only times IncidentOps already has; it carries no ground
  truth.
