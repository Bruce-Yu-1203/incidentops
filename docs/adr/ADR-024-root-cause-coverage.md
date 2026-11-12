# ADR-024: Root-cause/action consistency and expanded root-cause coverage

- **Status:** Accepted
- **Relates to:** architecture §9.1 (policy), benchmark.md §5, ADR-020

## Context

Policy checked an action's type, environment, target and arguments, but not
whether the action fits the diagnosis. A model could diagnose
`cpu_saturation` and still propose a perfectly allowlisted rollback, and the
proposal would reach a human approver. Several root causes in the taxonomy
also had no benchmark scenario, alert or remediation, and some tools had blind
spots (logs only for the HTTP API, no Redis health view).

## Decision

### Root cause -> action mapping (policy `p3`)

- `config/policy.yaml` gains a versioned `root_cause_actions` map from each
  taxonomy root cause to the only action types that may remediate it. The
  policy schema rejects a mapping that names an action without a rule.
- The proposal evaluator passes the diagnosis' `root_cause_id` to the policy
  engine. An action type that is not listed for that root cause is denied with
  reason `action_not_allowed_for_root_cause`; root causes with an empty list
  (`consumer_slow`, `application_error`, `insufficient_evidence`) or unknown
  root causes never get an approval request. Denials leave the incident in
  `DIAGNOSED_ONLY` for human review, as every policy denial does.
- The check runs at proposal time only. The executor's pre-dispatch re-check
  passes no root cause: the diagnosis is immutable and the envelope pins the
  policy version, so a changed mapping already blocks dispatch
  (`policy_version_changed`).
- The mapping restricts action *types*; targets are still restricted by the
  service catalog. The investigation prompt states the mapping
  (`investigation-v5`).

Later sections of this ADR record the remaining coverage decisions as they are
implemented.
