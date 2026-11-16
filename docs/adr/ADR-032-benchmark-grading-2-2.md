# ADR-032: Benchmark grading 2.2, multi-fault scenarios

- **Status:** Accepted (Amy approved the recommendations on 2026-11-16)
- **Relates to:** ADR-026 (grading 2.1), ADR-030 (groups), ADR-031 (prompt v12);
  `docs/proposals/2026-11-15-grouping-scenarios-grading-prompt.md` (sections 2, 3)

## Context

Grading 2.1 judges every diagnosed incident of a trial against one expected
fault. With incident groups (ADR-030) a fault is diagnosed once, by its group
primary; members have no diagnosis, and a replaced primary keeps one as
context. Testing the grouping guards also needs trials with two independent
faults, which manifest schema 1 cannot express.

## Decision

1. **Manifest schema 2:** `faults: [{fault_id, inject, expected,
   expected_group}]`, at least two with unique ids; no top-level `inject` or
   `expected`. Schema 1 stays valid and `fault_list()` gives both the same
   shape. `expected_group` is `primary` (default: the schema 1 alerting service,
   or the fault's `affected_component`) plus `may_include`. Ground truth stays
   benchmark-controller only; `leaks_ground_truth` checks every fault.
2. **Runner:** injects the faults in order (a failed injection resets the lab
   and makes the trial `INVALID: injection_failed:<fault>:<error>`), probes per
   fault with the fault's own window, waits for the longest recovery timeout,
   and records `time_to_primary_diagnosis_s` and `tokens_per_fault`.
3. **Cases:** B-F14 (`postgres_outage` + faulty release), B-N03
   (`postgres_outage` + `start_cpu_load`; CPU rather than a memory leak
   because its alert fires inside the 3-minute window), B-N04 (control: the
   B-F05 fault alone). B-F02, B-F09 and B-F12 declare their `may_include`
   services. Across the two faults of a case, one fault's fix is never in the
   other fault's prohibited list.
4. **Grading 2.2** as in `docs/benchmark.md` §6.6: faults judged by their
   diagnosing incident (group primary or split-out incident); wrong merge never
   split fails; missed merge is secondary; grouping metrics and per-fault
   `split_path` reported. Without group data every incident stands alone, so
   2.2 grades old-style observations like 2.1 except that a replaced primary's
   diagnosis is no longer a contradiction.
5. **Side by side:** single-fault records carry `status_2_1` and
   `diagnosis_correct_2_1`; the report's "Incident groups (grading 2.2)" table
   shows PASS 2.2 and PASS 2.1 per case.

## Consequences

- Runs graded 2.2 (with prompt v12 and grouping on) are not 1:1 comparable
  with r1/r2. The 2.1 column bounds how much of a difference is grading.
- `case_id` B-N03 replaces the optional "metrics within normal variance"
  idea in `docs/benchmark.md`, which moves to B-N05.
- No run with these cases has been made yet; the planned next step is one
  smoke trial each of B-F02, B-F09, B-F12, B-F14, B-N03 and B-N04.
