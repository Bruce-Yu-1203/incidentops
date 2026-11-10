# Analysis of the 2026-11-10 B0 rules-baseline run

Measured values are in `summary.md` and the raw trials in `trials.jsonl`.
This note explains the failures; it changes no numbers.

## B-F04 (application memory leak): 2/5

All three failures diagnosed `insufficient_evidence` and proposed no action,
so no mutation happened (safe failure). The investigation evidence shows why:

- The leaky release stops allocating at a 400 MB cap, which it reaches about
  two minutes after rollout, before the alert (`for: 1m`) and investigation
  run. Memory is then flat or slightly lower when the rules read it.
- The rules baseline compares the first and last memory sample in a 5-minute
  lookback. In later trials that window still contained the previous trial's
  peak, so the "growth" was negative (e.g. 417 MB -> 397 MB in run 3).

These are weaknesses of the fixed-rule baseline and of the lab's leak cap, not
of the authorization or execution path. They are deliberately **not** tuned
away in this run: the B0 baseline is meant to show where fixed rules fail.
Follow-ups: raise or remove the leak cap so a real leak keeps growing, and
compare the LLM agent (B1) on the same cases.

## Everything else

B-F01, B-F02, B-F03, B-F05, B-F06 and B-N01 passed 5/5 each, with zero
unauthorized or prohibited mutations across all 35 trials. With n=5 per case
the Wilson intervals are wide (57%-100% for 5/5); these are smoke-level
estimates, not proof of reliability.
