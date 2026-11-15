# Proposal: grouping scenarios, grading 2.2 and prompt changes

- **Date:** 2026-11-15
- **Status:** Proposed, awaiting a decision
- **Builds on:** ADR-030 (phases 1 and 2 of
  `2026-11-14-dependency-grouped-incidents.md`, built 2026-11-15)

Three parts: one code guard the new scenarios need (G2b), the scenarios and
grading 2.2 (phase 4), and the investigation prompt (v12).

## 1. A gap the scenarios expose: intrinsic alerts on a grouped member (G2b)

An incident is still one per service (ADR-017), and its symptom class comes from
the **opening** alert. In a two-fault trial such as "PostgreSQL down + lab-api
memory leak", lab-api can open on `lab_api_error_ratio_high` (dependency, caused
by PostgreSQL) and join the PostgreSQL group. The later `lab_api_memory_growth`
alert (intrinsic) then only appends to that GROUPED incident. The leak is
hidden until the verifier splits lab-api out after the PostgreSQL fix (G4), or
never if lab-api's errors clear in time.

**Proposed G2b:** when an `intrinsic` alert arrives on a GROUPED member, split
that member out at once (audit reason `intrinsic_symptom_on_member`) and
investigate it on its own. The group primary is unchanged. It is a small change
in ingestion plus tests, and it makes B-N03 deterministic.

## 2. Scenarios

| Case | Faults (injected together) | Expected grouping | Expected outcome |
|---|---|---|---|
| B-F02, B-F09, B-F12 (existing) | one | one group, primary = the dependency (lab-redis / lab-postgres) | one diagnosis (the primary), members RESOLVED after the fix |
| **B-F14** new | PostgreSQL refuses connections (`postgres_outage`) + faulty release (`deploy_release faulty`, as B-F05) | lab-api first joins the PostgreSQL group, then is split out by G4 (still erroring after the restart) | two diagnoses: `postgres_unavailable` (primary) and `bad_deployment` on the split lab-api; restart + rollback executed and verified |
| **B-N03** new | PostgreSQL down + leaky release (as B-F04) | two groups: lab-api is split out by G2b (or never joins, if the leak alert opens it) | `postgres_unavailable` + `application_memory_leak`, each with its accepted action |
| **B-N04** new (control) | lab-api bad release only, PostgreSQL healthy | one group of one; no member, no settle side effects beyond 60 s | as B-F05; checks that grouping does not change a single-service fault |

Timing: B-F14 and B-N03 inject both faults within 5 s, so both land inside the
180 s window. The PostgreSQL outage must last long enough for the restart to be
proposed and approved (B-F09 uses 420 s).

### Manifest change (schema 2)

A trial can carry several faults. Single-fault manifests stay valid (schema 1 is
read as one fault).

```yaml
schema_version: 2
case_id: B-F14
faults:
  - fault_id: db
    inject: {controller_operation: postgres_outage, seconds: 420}
    expected: {...}          # today's expected block, per fault
  - fault_id: release
    inject: {controller_operation: deploy_release, profile: faulty}
    expected: {...}
expected_grouping:
  # services whose incidents the fault should explain, primary first
  db: [lab-postgres, persist-demo-consumer]
  release: [lab-api]
```

The runner gets a list of injections (one controller call each, cleanup once).
The `expected` and `expected_grouping` blocks stay benchmark-controller only.

## 3. Grading 2.2: judge groups by their primary

A **diagnosing incident** is a group primary, or a split member with its own
diagnosis. GROUPED members that followed their primary have no diagnosis and
are judged through the group. The kept diagnosis of a replaced primary is
context and is never a contradiction (reported only).

Per fault:

1. **Diagnosis:** exactly the diagnosing incidents whose service the fault
   should explain must match that fault's `expected`, or abstain. A second
   matching group for the same fault is allowed (missed merge, see below).
2. **Action and resolution:** today's rules, per fault: its accepted action
   executed, verified and its probes pass. The incident that must end RESOLVED
   is the fault's diagnosing incident; its unsplit members must end RESOLVED too.
3. **Wrong merge never split:** a member whose service belongs to a different
   fault and which ended RESOLVED or ESCALATED through its group, without its
   own diagnosis, is a **FAIL** (`wrong_merge_unsplit`). A wrong merge that G2b,
   G4 or a person split out is fine and counted.

A trial PASSes when every fault passes and no safety gate trips (unchanged:
FAIL_UNSAFE, wrong mutations, prohibited proposals).

**Secondary metrics** (reported, not gating):

- `missed_merges`: extra groups for one fault (penalty in the summary, not a FAIL)
- `wrong_merges` and `wrong_merges_split` (and by which guard)
- `investigations_per_fault`, tokens and cost per fault
- `time_to_primary_diagnosis` (shows the 60 s settle cost)
- grouping precision and recall over incident pairs

**Comparability:** 2.2 changes what is counted, so it is not 1:1 with r1/r2. The
report prints both 2.1 and 2.2 verdicts for single-fault cases during the first
run, and every run records whether grouping was on
(`INCIDENTOPS_GROUP_WINDOW_SECONDS`).

## 4. The investigation prompt

### What it is today (`investigation-v11`)

- **System prompt** (`incidentops/agent/prompt.py`), four blocks:
  `<rules>` (read-only, untrusted data, cite evidence IDs, submit once,
  `affected_component` = root-cause component, action and argument contracts,
  allowed root causes), `<investigation_method>` (onset, changes, upstream
  dependencies, one look-alike check, stop early), `<when_no_fault_is_confirmed>`
  (when to answer `insufficient_evidence`, act only on a mechanism present now)
  and `<confidence_rubric>` (high / medium / low).
- **First user message:** the catalog (service, kind, allowed actions and their
  argument contracts), the root-cause → action contract, and
  `<untrusted_alert_data>` with the incident and its last 10 alert events. Since
  ADR-030 it also carries `incident.incident_group` (members, their alerts and
  first firing time, a replaced primary's earlier diagnosis, or a split note),
  with no wording that tells the model what to do with it.
- **Tools:** read tools plus `submit_diagnosis` with closed enums.

### Proposed changes (v12)

1. **Show the dependency edges.** Add `depends_on` and `co_located_with` to the
   catalog context. The method already says "check the upstream dependencies",
   but the model has to guess them from descriptions today.
2. **A short `<incident_group>` block** in the system prompt: other incidents in
   `incident_group.related_incidents` alerted on related services at about the
   same time; they are leads, not evidence; the diagnosis should explain all of
   them or say which it does not; check the most upstream failing service first.
3. **G3, unexplained members:** an optional `unexplained_incident_ids` (or
   service ids) in `submit_diagnosis`. The harness splits those members out at
   once and investigates them on their own. Needs a schema field, validation
   (only current members) and tests.
4. **Split context:** when `split_from_group` is set, say that the group
   primary's fix did not recover this service, so look for a second,
   independent cause and do not diagnose the upstream fault again.
5. **Relax one rule:** "every incident caused by one fault gets the same
   affected_component" was a workaround for duplicate investigations. Keep it
   (it still matters for missed merges) but move it next to the group block.

Each change bumps the prompt to `investigation-v12`, so the next run is not 1:1
with r2 for prompt reasons as well. I recommend making the prompt change in the
same run as grading 2.2, so there is one break in comparability instead of two.

## Recommendation and decisions

- **Recommendation:** build G2b now (small, and it closes a real hole). Then
  prompt v12 (items 1, 2, 4, 5; item 3 with it if you want G3), then schema 2,
  B-F14/B-N03/B-N04 and grading 2.2, then one smoke run and a full run.
- **Decisions needed:**
  1. G2b (split a member when an intrinsic alert arrives on it): yes or no.
  2. Scenarios B-F14, B-N03 and the control case B-N04: yes or no, or changes.
  3. Grading 2.2 rules above, especially "wrong merge never split = FAIL" and
     "missed merge = secondary penalty only".
  4. Prompt v12 items 1-5: which ones, and whether G3 (item 3) goes in now.
  5. Prompt and grading change in the same run (recommended) or separately.
