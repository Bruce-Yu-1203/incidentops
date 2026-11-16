# Proposal: prompt v15, affected_component for changes and inputs, stricter application_error

- **Date:** 2026-11-16
- **Status:** Approved by Amy on 2026-11-16 and built (ADR-034). Not measured yet: Amy
  asked to hold the planned 7 x 5 run.
- **Evidence:** `results/20261116T080000Z-B1_DEEPSEEK/` (full 17 x 5 run, v14, 70/85 PASS)

## Problem

All 15 failures of the v14 run were in three cases, 0/5 each:

| Case | v14 diagnosis | Expected | Action and recovery |
|---|---|---|---|
| B-F11 traffic surge | `traffic_surge` on `lab-loadgen` | `lab-api` | right action, resolved |
| B-F13 config error | `config_error` on `lab-api-config` | `lab-api` | right action, resolved |
| B-N01 no fault | `application_error` on `lab-api` (mostly high), no action | `insufficient_evidence` | nothing executed |

In r2 (v10) the same cases scored 4/5, 4/5 and 2/5.

- **Component.** The schema already said "a service rather than its configuration", but v11
  ("the component where the root cause lives"), v12 (dependency text) and v14 (root-cause
  definitions) pulled the model toward naming the source of the problem. Incident practice
  separates the two: the incident is about the service that fails, and the cause (a config
  push, a client, a noisy neighbour) is recorded as the cause. The fix target already has its
  own field, `proposed_action.target_id`.
- **No fault.** The v14 `application_error` definition ("fails without a recent change ...
  dependencies, releases and configuration healthy") also describes errors that have
  already stopped or that are within normal variation, so the model stopped abstaining.

Alternative considered and rejected: accept the source components as correct in the
grading (a list of accepted components). It changes the benchmark's answers instead of
making the prompt consistent with the schema, and it would not be comparable with r1/r2.

## Change (v15)

1. `<rules>`: affected_component is the registered service where the failure shows. A broken
   dependency (database, cache or broker down, locked or out of connections) is named itself.
   A change or input to a service (its configuration, traffic from its clients, load running
   in its process) names the service that runs with it; the cause goes in `root_cause_id`, the
   summary and `proposed_action.target_id`. The schema field description says the same.
2. Root-cause definitions: `config_error`, `traffic_surge` and `cpu_saturation` each say which
   service is the affected component.
3. `application_error` needs errors still present in the latest samples and clearly above the
   service's normal variation; otherwise `insufficient_evidence`.

Wording is generic (no case ids, no injection details) and passes
`tests/unit/test_root_cause_definitions.py`.

## Measurement (on hold)

Planned: B-N01, B-F06, B-F11, B-F13, B-F01, B-F09, B-F12 x 5 with grading 2.2, compared with
the 08:00Z v14 run. The dependency cases (B-F01, B-F09, B-F12) check that the new rule did not
move them away from naming the broken dependency.
