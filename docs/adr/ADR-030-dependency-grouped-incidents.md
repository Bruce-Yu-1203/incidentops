# ADR-030: Dependency-grouped incidents

- **Status:** Accepted (phases 1 and 2; Amy approved building them on 2026-11-15)
- **Relates to:** ADR-017 (dedup key), ADR-021 (handover), ADR-026 (grading 2.1);
  catalog `c5`; prompt `investigation-v11` (unchanged);
  `docs/proposals/2026-11-14-dependency-grouped-incidents.md`

## Context

An incident is keyed by `environment:service_id` (FR-002, ADR-017). One fault
in a shared dependency therefore opens one incident per alerting service: in
the r2 run B-F02, B-F09 and B-F12 opened two or three incidents per fault, each
with its own investigation, diagnosis and possibly a proposal (20
`duplicate_active_action` denials, and conflicting diagnoses). The proposal
linked above describes the problem, the direction convention ("A depends on
B": B is upstream) and the guards. This ADR records what was built and the
choices made where the proposal left room.

## Decision

### 1. Catalog `c5`: dependencies and co-location

`ServiceEntry` gains `depends_on` (the services it calls or uses) and
`co_located_with` (services sharing its runtime). Validation rejects unknown
ids, edges across environments, self references and `depends_on` cycles.
Edges: lab-api → lab-postgres, lab-redis, lab-api-config; persist-demo-consumer
→ lab-kafka, lab-postgres; demo-producer → lab-kafka; lab-loadgen → lab-api;
lab-cpu-load co-located with lab-api. A contract test checks `depends_on`
against the connection settings in `fault-lab/compose.yaml` (lab-api-config
lives inside lab-api and has none).

`related(a, b)` is a direct edge in either direction or co-location; grouping
uses the graph undirected. `upstream_rank` is 0 without dependencies, else one
more than the deepest dependency; the lowest rank is the most upstream. The
edges are not shown to the model yet (no prompt change in this phase).

### 2. Alert symptom class (guard G2)

Every rule in `infra/prometheus/rules/lab.rules.yml` has a `symptom_class`
label. `dependency`: lab_api_error_ratio_high, lab_api_latency_p95_high,
lab_kafka_consumer_lag_high, lab_consumer_processing_slow. `intrinsic`: all
others. Normalization stores it on `AlertEvent`, and the opening alert's class
on the incident. A missing or unknown value is `intrinsic`, so an unlabelled
alert is never grouped under another service.

**G2b (added 2026-11-16):** an incident is one per service and takes its class
from the opening alert, so a member that joined on a dependency symptom could
hide a second fault: a later intrinsic alert would only append to it. Now a
**firing intrinsic alert on a GROUPED, unsplit member splits it out at once**,
in the ingestion transaction: `GROUPED` → `RECEIVED` with an investigation job,
audit `incident_group_split` with reason `intrinsic_symptom_on_member: <alert>`
and note "an intrinsic symptom started on this service while grouped". The
group and its primary are unchanged.

### 3. Groups and the `GROUPED` state

Migration `0012` adds `incident_groups(id, environment, primary_incident_id,
opened_at, window_ends_at)` and, on `incidents`, `group_id`, `group_split_at`,
`group_note` and `symptom_class`. With grouping on, every new incident belongs
to a group (often a group of one). A split member keeps `group_id` for history
and gets `group_split_at`; it no longer counts as a member.

`GROUPED` is a non-terminal incident state, so later alerts of that service
keep appending to the same incident (the active-dedup index is unchanged).
Edges: `RECEIVED`/`TRIAGED` → `GROUPED` (joined before its own run started),
`DIAGNOSED` → `GROUPED` (a replaced primary's finished run), `GROUPED` →
`RECEIVED` (split), `RESOLVED` (verifier), `ESCALATED` (follows the primary),
`FAILED`, `CANCELLED`. `INVESTIGATING` → `GROUPED` does not exist: a running
investigation always finishes first.

### 4. Grouping at ingestion

In the ingestion transaction, after the advisory lock
`pg_advisory_xact_lock(hashtext('incident_groups:<environment>'))` (taken
before any row lock, so concurrent deliveries serialize without deadlock), a
new incident N on service S:

1. **Candidate (G1):** the oldest group that has an active, unsplit incident on
   a service related to S, opened within `INCIDENTOPS_GROUP_WINDOW_SECONDS`
   (default 180). None: N opens its own group.
2. **N more upstream than the primary P:** N becomes the primary if P was
   opened by a `dependency` symptom and is `RECEIVED`, `TRIAGED` or
   `INVESTIGATING`. If P's run had not started, P becomes `GROUPED` and its
   pending investigation job is retired (`DONE`, `last_error = cancelled: ...`;
   a leased job finds the incident `GROUPED` and does nothing). If P's run is
   going, it finishes; its diagnosis is kept and the incident goes
   `DIAGNOSED` → `GROUPED` with no `evaluate_action_proposal` job. If P was
   opened by an intrinsic symptom, or is already past `INVESTIGATING` (a
   proposal may be in flight), N opens its own group instead, audited as
   `incident_not_grouped`.
3. **Otherwise:** an `intrinsic` N opens its own group (G2); a `dependency` N
   joins as a member: `RECEIVED` → `GROUPED`, no investigation job.
4. **Settle:** a primary whose service has `depends_on` gets its investigation
   job `available_at = now + INCIDENTOPS_GROUP_SETTLE_SECONDS` (default 0, see below), also
   when it takes over a group; the most upstream services are investigated
   immediately.

   **Default changed to 0 s on 2026-11-16 (Amy):** in r2 and in the
   grouping smoke the upstream alert always arrived first (11-35 s ahead),
   so the wait never helped, while every fault without an upstream alert
   (bad release, configuration, memory, CPU, traffic, locks) lost 60 s
   (B-N04: 74 s to diagnosis). A late upstream incident still takes over:
   the downstream run already going finishes as context without a proposal,
   which costs at most one extra investigation.

`window_ends_at` records the newest member's opening time plus the window.
`INCIDENTOPS_GROUP_WINDOW_SECONDS=0` turns grouping off (each incident alone,
`group_id` NULL, the pre-ADR behaviour). Audit events: `incident_grouped`,
`group_primary_changed`, `group_primary_replaced`, `incident_not_grouped`,
`incident_group_split`, `group_member_resolved`.

### 5. Investigation context

The primary's context gets `incident.incident_group` inside the existing
`<untrusted_alert_data>` block: each unsplit member's service, state, alert
names, first firing time and, for a replaced primary, its earlier diagnosis
(root cause and summary). A split member gets `split_from_group` and its note
("not explained by the group primary's fix"). This is data only; the prompt
text and `PROMPT_VERSION` (`investigation-v11`) are unchanged. Prompt guidance
on how to use the list (and G3, members the primary cannot explain) is proposed
to Amy separately.

### 6. Members follow the primary

`persistence.transitions.transition()` calls `grouping.on_incident_terminal`
when an incident reaches a terminal state, in the same transaction, so every
path (agent, executor, verifier, API) is covered:

| Primary ends | GROUPED members |
|---|---|
| `RESOLVED` | one `verify_group_members` job each, for the verifier (G4) |
| `ESCALATED`, `DIAGNOSED_ONLY`, `FAILED` | alerts resolved: `ESCALATED`, `escalation_reason = group_primary_escalated`; alerts still firing: split out (fix A, below) |
| `CANCELLED` | split out and investigated on their own |

**Fix A (2026-11-16, found by the B-F14 smoke):** a handed-over primary has no
verified fix, so it cannot vouch for its members. In B-F14 the PostgreSQL
restart's verification also checks lab-api's error ratio, which a second,
independent fault (a bad release) kept high; the primary was escalated, lab-api
followed it, and G4 never ran. Now a member whose latest delivery of any alert
is still firing is split out instead (`GROUPED` → `RECEIVED`, investigation
job, reason `primary_escalated_member_still_firing`, note "still alerting when
the group primary was handed to a person"); members whose alerts resolved
still follow into `ESCALATED`. A member of the same fault may therefore get
its own diagnosis of the same root cause; that is a missed merge (reported),
not a failure. Migration `0013` lets the executor read alert events, because
it escalates primaries when an action fails.

**B' (2026-11-16, Amy):** the primary's verification no longer judges its
members. A metric check on another service (for example
`api_errors_recovered` on lab-api in a PostgreSQL restart) is skipped while
that service is an unsplit `GROUPED` member of the primary's group; the check
is stored with `skipped: true` and the `verification_completed` audit names
it ("delegated to group member checks: ..."). A skipped check never counts
as passed, and a verification with only skipped checks does not pass. The
member is then judged by its own alerts (G4). Without a group, or when the
service is not a member (no incident, split out, or its own group), the check
runs as before. In the B-F14 rerun before this change the PostgreSQL fix was
correct but escalated, because the bad release kept lab-api's errors high.

The verifier job waits up to `INCIDENTOPS_GROUP_MEMBER_RECOVERY_SECONDS`
(default 180, polling every 15 s) for each of the member's alert fingerprints to
have a latest delivery of `resolved` (Alertmanager reports a cleared rule one
`group_interval` later). Recovered: `GROUPED` → `RESOLVED`, so only the verifier
resolves. Still firing: split, `RECEIVED` with an investigation job whose
payload and incident note say "not explained by the group primary's fix". A
replaced primary whose run finishes after the new primary ended follows it at
that point. A replaced primary whose run fails ends `FAILED` on its own.

The verifier handles jobs one at a time, so a member wait can delay the next
verification by up to the recovery time; acceptable for the lab.

### 7. Approvals

The action envelope and its hash never contain the member list; a member
joining while an approval is pending leaves the hash valid (integration test).

### 8. API and console (G5)

Incident responses carry `group_id`, `primary_incident_id`, `group_split_at`,
`group_note` and `members` (the group's other incidents, split ones flagged).
`POST /api/v1/incidents/{id}/ungroup {expected_version, reason?}` lets a
signed-in operator split a member out (version-checked, audited as
`user:<name>`). A replaced primary still in its run is marked split without a
state change and is routed normally, with its proposal, when the run ends. The
console shows "+N related" on a primary's inbox card, "grouped" on a member's,
a link from a member's page to its primary and the primary's member list.

### 9. Privileges

Migration `0012` grants: API `SELECT, INSERT, UPDATE` on `incident_groups`;
agent, executor and verifier `SELECT`. The verifier additionally gets `SELECT`
on `alert_events` (member alert status) and `INSERT` on `outbox_jobs` (the
split's investigation job). No role gains write access to actions, approvals
or executions.

## Consequences

- One investigation per fault for the storm cases (B-F02, B-F09, B-F12), when
  the dependency alerts first or within the settle time. Not yet measured: no
  benchmark has run with grouping.
- **Benchmark comparability:** grading 2.1 (ADR-026) still judges each
  incident. Grouped members have no diagnosis of their own, and a replaced
  primary's kept diagnosis still counts. Grading 2.2 (judge groups by the
  primary) comes after Amy reviews its design; until then, runs that must
  compare 1:1 with r1/r2 should set `INCIDENTOPS_GROUP_WINDOW_SECONDS=0`.
- Unrelated faults in related services within 3 minutes can be grouped. G2
  keeps intrinsic symptoms out, G4 splits members the fix did not recover, G5
  lets a person split; G3 waits for the prompt change.
- The symptom class needs the reloaded Prometheus rules. Until then alerts
  arrive without the label and are intrinsic: never grouped as members.

## Alternatives considered

- **Hard-merge into one incident:** loses per-service dedup, state and audit,
  and a wrong merge cannot be undone cleanly. Rejected (proposal decision 2).
- **Alertmanager `inhibit_rules`:** inhibited alerts never reach IncidentOps,
  so G4 could not check members and the audit would lose the symptoms.
- **A grouping sweep in the control worker** instead of the transition hook:
  members would lag their primary and need another at-least-once loop; the
  hook keeps them consistent in one transaction.
