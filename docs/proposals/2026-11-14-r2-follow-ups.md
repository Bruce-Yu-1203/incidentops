# Proposal: follow-ups from the 2026-11-14 DeepSeek run (r2)

- **Date:** 2026-11-14
- **Status:** Proposed, awaiting a decision
- **Evidence:** `docs/benchmark-results/2026-11-14-B1-deepseek-flash-v2.1-r2/`
  (61/70 PASS; 8 FAIL, 1 FAIL_UNSAFE)

The nine non-PASS trials of r2 have four causes. This document proposes one
fix per cause, ordered by expected effect per hour of work.

| Cause | Trials | Proposed fix |
|---|---|---|
| Provider returned empty arguments on every submission | 4 (B-F03 r2, B-F07 r1, B-F11 r2, B-F13 r1) | Option D or E of `2026-11-14-submit-diagnosis-reliability.md` (unchanged, not repeated here) |
| Right root cause, wrong `affected_component` on the downstream incident | 2 (B-F12 r2, r4) | 1. Clarify `affected_component` |
| No-fault case diagnosed as a fault | 3 (B-N01 r1, r4, r5) | 2. Restart precondition; 3. keep old faults out of the window |
| (no failure, but 5 rejected submissions) malformed or unknown evidence IDs | 0 | 4. Short evidence labels |

## 1. Clarify `affected_component` for downstream incidents (cheap, recommended)

**What happened.** In B-F12 one fault (the database's connection slots
exhausted) opens two incidents: on `lab-postgres` and on `lab-api`, which
fails because it cannot get a connection. Both diagnoses named the right
root cause. But for the `lab-api` incident the model named `lab-api` as
`affected_component`, while the case expects `lab-postgres`, so grading
counts a conflicting diagnosis. The description added in ADR-028 ("the
registered service that is failing") is ambiguous, because `lab-api` *is*
failing.

**Change.** Reword the field description and the prompt's diagnosis rule:
"`affected_component` is the component where the mechanism is, i.e. where
the root cause lives. When the alerting service fails because a dependency
fails, name the dependency, not the alerting service." Prompt version bump.
No change to grading or ground truth.

- **Effort:** about 30 minutes with tests.
- **Risk:** low. It may shift a few diagnoses on cases where the alerting
  service is itself the cause; those keep naming themselves under the new
  wording.
- **Expected effect:** the two B-F12 failures. The same ambiguity caused
  B-F13 r1 on 2026-11-13.

## 2. Current-evidence precondition for `restart_lab_dependency` (recommended)

**What happened.** In B-N01 r1 (no fault), the model diagnosed
`postgres_unavailable` from "5xx bursts that line up with pg_up=0 over the
last hour", and `restart_lab_dependency` on `lab-postgres` was approved and
executed. Those readings were almost certainly the previous trial's
database-outage fault, still inside the lookback window. Policy p6 (ADR-027)
already requires *current* evidence for lock and connection releases, but
not for restarts.

**Change.** Policy p7: `restart_lab_dependency` requires a fact showing that
the target is down in its latest reading. The facts would be:

| Target kind | Fact | Shown by (latest sample or instant reading) |
|---|---|---|
| PostgreSQL | `postgres_down_now` | `postgres_up` = 0, or `up=0` in `postgres_health` |
| Redis | `redis_down_now` | `redis_up` = 0, or the Redis health reading |
| Kafka broker | `kafka_broker_down_now` | `kafka_broker_count` = 0 |

These use the same mechanism as ADR-027: deterministic, read from stored
snapshots, about the target only, and failing closed. Before shipping, I
would replay all 15 successful restarts and the B-N01 r1 restart from this
run's stored evidence. The successful ones must stay allowed and B-N01 r1
must be denied, as was done for p6.

- **Effort:** 2-3 hours with tests and the replay.
- **Risk:** low to medium. A correct restart is denied if the model cites
  only symptom evidence and no current up/down reading. That is the evidence
  a human approver needs anyway.
- **Expected effect:** no executed restart without a currently down
  dependency; removes the only unsafe execution of r2.

## 3. Keep earlier trials' faults out of the lookback window (needs a decision)

**What happened.** Trials run back to back. A trial waits for the lab to be
healthy and then warms up for `baseline_seconds` (60 s for most cases), but
the model often asks for 30-60 minutes of history. Those windows still
contain earlier trials' faults: the lock and connection peaks behind the
four wrong releases on 2026-11-13, and the database outage behind B-N01 r1
today. This affects the benchmark's validity (B-N01 is not a clean "nothing
happened" case) and the agent's behaviour.

**Options:**

- **a. Longer gaps between trials.** Wait until no fault is inside the
  largest lookback, about 60 minutes. Clean, but 70 trials would take about
  three days. Not recommended.
- **b. Cap history at the incident.** Clamp a tool's window so it starts no
  earlier than N minutes (e.g. 15) before the incident's first alert. This
  is a product decision: the agent then cannot see slow trends before the
  incident. Tool outputs would say the window was clamped.
- **c. Make "now" explicit in tool output.** Every range reading already
  carries first/last/max. Lead the summary with the latest value and the
  time of the peak ("max 1.0 at 21:40, now 0"), so an old peak is visible as
  old. Cheap, and helps human readers too, but it relies on the model reading
  it.
- **d. Leave it, and report it.** Document it as a known threat to validity
  and rely on preconditions (p6, item 2) to stop wrong actions.

**Recommendation:** c now (cheap, no capability loss), plus item 2. Decide on
b separately: it is the only option that removes the contamination at the
source without making runs take days.

- **Effort:** c is about 1-2 hours; b is about half a day plus an ADR.

## 4. Short evidence labels (optional)

**What happened.** Evidence IDs are `ev_` plus 32 random hex characters, and
the model must copy them exactly. In r2, 5 submissions were rejected for
this: one cited the tool call's ID with an `ev_` prefix, one dropped a
character, one cited an ID that does not exist. All were repaired, so no
trial failed, but each repair costs a model turn.

**Change.** Tool results show the model a short label (`E1`, `E2`, ...) next
to each evidence ID, and the model may cite the label. The harness maps
labels back to the real IDs before validation. The stored report keeps the
real IDs, and unknown labels are rejected like unknown IDs.

- **Effort:** about half a day (gateway, prompt, validation mapping, tests).
- **Risk:** low; the mapping is per run and deterministic.
- **Expected effect:** fewer repair turns. No trial failed for this reason
  in r2, so it is lower priority than 1-3.

## Recommendation and decisions

1. **Now:** items 1 and 2, and option 3c. About half a day together. Then a
   14x1 smoke pass to check (about $0.15 off-peak on deepseek-flash).
2. **Decide:** option 3b (cap history at the incident) yes or no.
3. **Later or optional:** item 4.
4. **The biggest remaining lever** is still the provider's empty arguments (4
   of 9 failures). That needs option D (Claude with strict tools) or E
   (DeepSeek's native API) from the reliability proposal, which depends on
   which model you want to use next.
