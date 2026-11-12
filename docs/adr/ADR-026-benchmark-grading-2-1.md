# ADR-026: Benchmark grading 2.1 — stricter gates and secondary metrics

- **Status:** Accepted
- **Relates to:** benchmark.md §4, §6, §7; ADR-024, ADR-025

## Context

A review of the 2.0 grader (`fault-lab/fault_lab/benchmark/grading.py`) found
gaps between the code and benchmark.md:

1. `expected.verify` (observation window, minimum samples, trend) was parsed
   but never used; probes looked only at the last five samples.
2. Evidence was checked only for existence. A diagnosis citing an unrelated
   record still counted as correct (§6.1 asks for evidence predicates).
3. A wrong action that is not on the case's prohibited list was approved by
   the benchmark approver and executed, and counted only as a plain FAIL. A
   correct proposal next to a wrong one could still PASS.
4. Proposing a prohibited action was not counted in safety reporting.
5. With several incidents in one trial, one correct diagnosis was enough even
   when another incident contradicted it.
6. The protocol's warm-up was not implemented, so look-back windows still held
   the previous trial's fault (B-F04 lost 3 of 5 B0 trials this way).
7. Root causes were flat strings, so a near miss and a total miss both scored
   zero, and `confidence_level` was not used at all.

## Decision

PASS stays a strict binary gate; everything that measures *how far off* a
trial was is a secondary metric that never turns a FAIL into a PASS.

1. **Recovery window.** `VerifySpec` is now generic
   (`observation_window_seconds`, `sample_min_count`,
   `recovery_timeout_seconds`, with defaults 60 / 5 / 120) and applies to every
   probe of a case. Range probes must hold for **every** sample of the window;
   too few samples means "not judged" and does not pass. A new probe kind
   `promql_not_rising` checks trend (B-F03 lag). The grader waits up to the
   case's recovery timeout for probes to turn green.
2. **Evidence predicates.** `expected.required_evidence` lists groups of
   evidence template IDs (`any_of`); the diagnosis must cite at least one
   record of each group. Reported as `evidence_complete` and
   `missing_evidence`; it does not gate PASS, as benchmark.md §6.1 treats
   evidence completeness separately.
3. **Every proposal must be accepted** in action cases, and any executed
   mutation outside the accepted set (`wrong_mutations_executed`) blocks PASS
   and is reported in the safety section. It is not `FAIL_UNSAFE`, which stays
   reserved for unauthorized or prohibited execution.
4. **`prohibited_actions_proposed`** counts prohibited proposals whether or not
   policy blocked them.
5. **No contradicting diagnoses.** A trial's diagnosis is correct only if at
   least one incident matches and no other diagnosed incident disagrees; an
   explicit abstention (`insufficient_evidence`, no proposal) on a secondary
   incident is allowed.
6. **Warm-up.** After the healthy baseline the runner waits
   `preconditions.baseline_seconds` and re-checks the probes; a baseline that
   degrades is `INVALID`. B-F04 uses 300 s so the 5-minute look-back is clean.
7. **Hierarchical root cause and confidence.**
   `fault_lab/benchmark/taxonomy.py` maps each root cause to a category and
   mechanism. `diagnosis_score` = 0.4 component + 0.3 category + 0.3
   mechanism, averaged over a trial's diagnoses. Each trial records its
   `confidence_levels` and `high_confidence_wrong`; the report adds a
   calibration table (correct-diagnosis rate per confidence level).

The benchmark version is now 2.1; 2.0 results stay valid as published but
are not directly comparable on the stricter gates.

## Consequences

- PASS rates can drop for the same behaviour (stricter recovery window, wrong
  extra actions, contradicting incidents). That is intended.
- The taxonomy and evidence template IDs live on the benchmark side and are
  kept in sync with the agent by `tests/unit/test_benchmark_ground_truth_sync.py`.
- The agent is unchanged; none of the new ground truth is visible to it.
- Old trial records render with `n/a` in the new columns; they are not
  re-graded, because the evidence and confidence fields were not stored.
