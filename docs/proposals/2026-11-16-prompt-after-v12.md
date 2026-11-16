# Proposal: what else to change in the investigation prompt after v12

- **Date:** 2026-11-16
- **Status:** Items 1-3 approved 2026-11-16 and built as investigation-v13 (ADR-033);
  root-cause definitions drafted for review; item 4 open
- **Evidence:** `docs/benchmark-results/2026-11-14-B1-deepseek-flash-v2.1-r2/`
  (61/70 PASS with `investigation-v10`; v11 and v12 not measured yet)

## First: measure v12 before changing more

v11 (ADR-029) and v12 (ADR-031) have not been run yet. Each further change
on top makes it harder to tell which one helped. **Recommendation:** run the
planned smoke (B-F02, B-F09, B-F12, B-F14, B-N03, B-N04) and one full 14x5
run with v12, then pick from the list below by what still fails. The items are
ordered by the r2 evidence behind them.

## Where r2 still lost trials

| Cause | Trials | Prompt can help? |
|---|---|---|
| Provider returned empty `submit_diagnosis` arguments on every attempt | 4 | Partly (item 1) |
| Right cause, wrong component on the downstream incident (B-F12) | 2 | Done in v11 and v12 (affected_component rule, groups) |
| No-fault control named a fault from older readings (B-N01) | 3 | Partly (item 2); restart precondition already in policy p7 |
| Malformed or unknown evidence IDs (repaired, no trial lost) | 0 (5 repairs) | Yes (item 3) |

## Candidates

### 1. Smaller, plainer `submit_diagnosis` arguments (recommended to test first)

30% of submissions arrived empty (42/138). The likeliest cause is the provider
failing to parse long argument text. A submission averages about 2,900 output
tokens. Prompt rules that may lower the risk:

- `summary` at most 3 sentences; each `evidence_notes` entry at most 1
  sentence; `unresolved_questions` at most 3 items.
- No line breaks, double quotes or backslashes inside string values; name
  metrics and services in plain words instead of quoting PromQL or log lines.
- Cite only the evidence that supports the conclusion (typically 3-6 IDs),
  not every reading taken.

The schema can enforce the sizes too (e.g. `summary` 600 characters instead of
2,000), but a hard limit turns a long answer into a rejected one. So prompt
first, schema limit only if the prompt alone does not shorten them.
**Measure:** the empty-argument rate and output tokens per submission against
r2 (30%, about 2,900). It also lowers cost: output is about 80% of the r2 bill.

### 2. Anchor "current" to the incident's opening time

B-N01 run01 restarted PostgreSQL on readings from the previous trial's outage,
inside the 30-minute lookback. v9/v10 say "latest samples" and policy p7 now
requires the dependency to be down now, but the model still decides what
"current" means. Proposal: the first message states `incident.created_at` and
the investigation's start time explicitly (both already known to the harness),
and one rule says: "A reading counts as current only if it is from the last
5 minutes before the investigation started; anything older is history that may
belong to an earlier, resolved problem." This is a prompt-only version of the
deferred history clamp (3b in `2026-11-14-r2-follow-ups.md`), without cutting
any tool's window. **Measure:** B-N01 (2/5 in r2).

### 3. Short evidence labels

Item 4 of `2026-11-14-r2-follow-ups.md`, not started: tool results show `E1`,
`E2`, ... next to each evidence ID, the model may cite the label, and the
harness maps labels back before validation. Removes copy errors of the 32-hex
IDs (5 repairs in r2) and shortens the submission (helps item 1).
It is a harness change with one prompt line.

### 4. Remove duplicated rules now that the schema carries them

Since ADR-028 B the tool schema lists the allowed root causes, components,
action types and argument values. The system prompt still repeats the root-cause
list and the `one_of` explanation, and the first message repeats every
service's argument contract. Dropping the duplicates shortens the prompt by
about a fifth and leaves one source of truth. Risk: low, but it is a change
with no failure behind it, so only together with another item.

### 5. Not recommended now

- **Worked examples (few-shot):** they help format, but any example close to a
  lab fault risks steering the model toward that answer (benchmark ground
  truth must not leak, benchmark.md §2).
- **Changing the confidence rubric:** `high` was right 57/60 in r2.
- **More investigation steps or a fixed order:** median run 47 s and 24 tool
  attempts per trial; no run hit the budget in a failing trial.

## Decisions needed

1. Measure v12 first (smoke, then a full run) before more prompt changes: yes or no.
2. Afterwards, which of items 1-4. Recommended: 1 and 3 together (both target
   the submission), then 2 if B-N01 still fails.
