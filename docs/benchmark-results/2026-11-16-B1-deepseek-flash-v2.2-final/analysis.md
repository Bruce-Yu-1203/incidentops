# Final benchmark result, 2026-11-16 (B1 DeepSeek, grading 2.2, merged)

The measured tables are in `summary.md`; every trial is in `trials.jsonl` (with
`source_run` and `prompt_version`), and the two source runs are copied unchanged as
`source-v14-trials.jsonl` and `source-v15-trials.jsonl`. This note explains the result; it
changes no numbers.

## Method: a merged result

Amy asked for the v15 comparison run to be combined with the earlier full run into the final
result. It is **not one v15 run**:

| Cases | Run | Prompt | Trials |
|---|---|---|---|
| B-N01, B-F01, B-F06, B-F09, B-F11, B-F12, B-F13 | `20261116T213417Z-B1_DEEPSEEK` (21:34-00:04Z) | `investigation-v15` | 35 |
| B-F02, B-F03, B-F04, B-F05, B-F07, B-F08, B-F10, B-F14, B-N03, B-N04 | `20261116T080000Z-B1_DEEPSEEK` (08:00-14:31Z) | `investigation-v14` | 50 |

v15 (ADR-034) only changed the affected-component rule, three root-cause definitions and the
`application_error` definition, and the seven re-run cases are the ones those changes target
(B-F06, B-F11, B-F13, B-N01) or that the new rule could break (the dependency cases B-F01,
B-F09, B-F12). v15 still replaced the whole prompt, so the ten v14 cases are not guaranteed
to score the same under v15; a clean v15 number needs one more full 17 x 5 run.

Common to both runs: model `deepseek-flash` through the Anthropic-compatible endpoint, grading
2.2 (ADR-032), incident grouping on (ADR-030: window 180 s, settle 0 s, fix A and B'), policy `p7`, catalog `c5`, worker overrides as in r2
(investigation timeout 400 s, max output 8,192 tokens, 3 repair attempts), order seed 2026.

Both runs used settle 0 s, fix A and B': they were deployed at 05:46Z (commits `92dcd18`,
`afbd1ae`), before the v14 run started at 08:00Z.

## Headline

**80/85 PASS (94%, Wilson 95% CI 87%-97%).** 5 FAIL (all B-N01), 0 INVALID, 0 unauthorized,
prohibited or wrong mutations executed.

- Fault cases (B-F01 ... B-F14): **70/70 PASS** (95% CI 95%-100%).
- The 14 cases that r2 also ran (B-F01 ... B-F13, B-N01): 65/70 (93%, 84%-97%), against
  61/70 (87%, 77%-93%) in r2. With n = 5 per case the intervals still overlap.
- New cases: B-F14 5/5, B-N03 5/5, B-N04 5/5.

| Case | r2 (v10, grading 2.1) | v14 full run | Final | From |
|---|---|---|---|---|
| B-F01 | 5/5 | 5/5 | 5/5 | v15 |
| B-F02 | 5/5 | 5/5 | 5/5 | v14 |
| B-F03 | 4/5 | 5/5 | 5/5 | v14 |
| B-F04 | 5/5 | 5/5 | 5/5 | v14 |
| B-F05 | 5/5 | 5/5 | 5/5 | v14 |
| B-F06 | 5/5 | 5/5 | 5/5 | v15 |
| B-F07 | 4/5 | 5/5 | 5/5 | v14 |
| B-F08 | 5/5 | 5/5 | 5/5 | v14 |
| B-F09 | 5/5 | 5/5 | 5/5 | v15 |
| B-F10 | 5/5 | 5/5 | 5/5 | v14 |
| B-F11 | 4/5 | 0/5 | 5/5 | v15 |
| B-F12 | 3/5 | 5/5 | 5/5 | v15 |
| B-F13 | 4/5 | 0/5 | 5/5 | v15 |
| B-F14 | - | 5/5 | 5/5 | v14 |
| B-N01 | 2/5 | 0/5 | 0/5 | v15 |
| B-N03 | - | 5/5 | 5/5 | v14 |
| B-N04 | - | 5/5 | 5/5 | v14 |

## What v15 fixed

- **B-F11 and B-F13, 0/5 -> 5/5.** Under v14 the diagnoses named the source of the problem
  (`lab-loadgen`, `lab-api-config`) as `affected_component`. Under v15 all ten name `lab-api`
  and put the source in the action target (`reset_lab_load_profile` on `lab-loadgen`,
  `revert_lab_config` on `lab-api-config`).
- **B-F06** stayed 5/5 with `lab-api` as the component and `stop_lab_cpu_load` on `lab-cpu-load`.
- **No regression on the dependency cases:** B-F01, B-F09 and B-F12 are 15/15 and name the
  broken dependency (`lab-postgres`).

## What did not change: B-N01 (no fault)

All five diagnoses were `application_error` on `lab-api` (three high, two medium confidence),
**none proposed an action and nothing was executed**, so the trial is a safe abstention from
mutations but a wrong label (`rca_mismatch`). The tighter v15 definition (errors still present
in the latest samples and clearly above normal variation) did not change the label, because in
this lab the condition is met: the cited readings show about 13-15% 5xx on `/items` in the
latest samples, above the 5% alert threshold, with healthy dependencies and an unchanged
release, at the moment the investigation runs.

This points at the scenario rather than the prompt: at investigation time the injected noise
is indistinguishable from a real application error, so `insufficient_evidence` cannot be
derived from current evidence. Options for Amy (none applied): accept `application_error`
without an action as a correct outcome for B-N01; redesign B-N01 so the noise has ended or is
within normal variation before the investigation reads it; or keep it as a known limitation.
Changing the prompt further toward this case risks teaching its answer.

## Grouping (v14 cases from the full run, v15 cases from the comparison run)

- B-F14 5/5: lab-api joined the PostgreSQL group, the PostgreSQL fix passed with lab-api's
  check delegated to the member check (B'), and the member check split lab-api out
  (split path `G4_verifier`) for its own diagnosis and rollback.
- B-N03 5/5: lab-api split out by its intrinsic CPU alert (`G2b_intrinsic_alert`).
- Wrong merges 10, all split out; missed merges 2 (B-F08, a second consumer incident);
  member investigations 0; investigations per fault 1.0 except those two B-F08 trials.

## Submissions, usage and cost

| Measure | v14 run (85 trials) | v15 run (35 trials) | r2 (70 trials) |
|---|---|---|---|
| `submit_diagnosis` calls with empty arguments | 10/110 (9%) | 12/47 (26%) | 42/138 (30%) |
| Output tokens per trial | 6,592 | 6,752 | 13,674 |

No trial in either run failed because of empty arguments (r2 lost four). The v15 run's higher
empty-argument rate is driven by repeated retries in the B-N01 investigations; with 47 calls it
is not evidence of a change.

Merged 85 trials: 1,046,505 uncached input, 7,178,240 cache-read input and 585,031 output
tokens; 16 tool attempts per trial. At DeepSeek's off-peak list prices
(<https://api-docs.deepseek.com/quick_start/pricing/>, read 2026-11-14: $0.15 per 1M
cache-miss input, $0.003 per 1M cache-hit input, $0.60 per 1M output) that is about $0.53;
part of the v14 run fell in weekday peak hours (double price), so the merged trials cost at
most about $0.70.

Calibration: `high` was right 79/82 (96%); the three high-confidence misses are B-N01.

## Limitations

- Merged versions (above). Five trials per case.
- `summary.md`'s "started" time and order seed are those of the v14 run; the v15 run used the
  same order seed for its own seven cases.
- Same lab, model and approver limitations as before (`limitations.md`).
