# ADR-034: Prompt investigation-v15: affected_component for changes and inputs, stricter application_error

- **Status:** Accepted (Amy, 2026-11-16)
- **Relates to:** ADR-029 (affected_component rule), ADR-033 (v13/v14);
  `docs/proposals/2026-11-16-prompt-v15-affected-component.md`

## Context

The v14 full run (`results/20261116T080000Z-B1_DEEPSEEK/`, 70/85) failed B-F11 and B-F13 5/5
because the diagnosis named the source of the problem (the client load generator, the
configuration store) as `affected_component`, while the scenarios, the schema description and
incident practice name the service that fails. B-N01 failed 5/5 with `application_error`
(no action), because the v14 definition also fit errors that were no longer current.

## Decision

1. `<rules>` and the `affected_component` field description: name the registered service where
   the failure shows; a broken dependency is named itself; for a change or input to a service
   (configuration, client traffic, co-located load) name the service that runs with it and put
   the cause in `root_cause_id`, the summary and `proposed_action.target_id`.
2. `<root_causes>`: `config_error`, `traffic_surge` and `cpu_saturation` state which service is
   the affected component; `application_error` requires errors still present in the latest
   samples and clearly above normal variation, otherwise `insufficient_evidence`.
3. `PROMPT_VERSION = "investigation-v15"`. Grading (2.2) and scenario files are unchanged.

## Consequences

- To be measured; the planned 7 x 5 run is on hold at Amy's request.
- The dependency cases (B-F01, B-F09, B-F12) must keep naming the broken dependency; the rule
  keeps that case first.
