# Proposal: group incidents by service dependency

- **Date:** 2026-11-14
- **Status:** Proposed, awaiting a decision
- **Evidence:** `docs/benchmark-results/2026-11-14-B1-deepseek-flash-v2.1-r2/`

## Problem

Today an incident is keyed by `environment:service_id` (ADR-007/FR-002). One
fault in a shared dependency therefore opens one incident per service that
alerts. In the r2 run, four cases did this every time:

| Case | Incidents opened by one fault (time after the first) |
|---|---|
| B-F02 (Redis down) | lab-redis, lab-api (+17-22 s) |
| B-F09 (PostgreSQL down) | lab-postgres, lab-api (+17 s), persist-demo-consumer (+20 s) |
| B-F12 (connection pool full) | lab-postgres, lab-api (+17-22 s) |
| B-F08 (consumer slow, 1 of 5) | persist-demo-consumer twice (+35 s) |

Each incident gets its own investigation (tokens and time), its own
diagnosis and possibly its own proposal. That causes:

- **Duplicate proposals:** 20 `duplicate_active_action` policy denials in r2.
  The second incident proposed the same fix while the first was in flight.
- **Conflicting diagnoses:** B-F12 failed twice because the lab-api incident
  named itself as the component (fixed in the prompt by ADR-029, but it is a
  symptom of investigating the same fault twice).
- **Noise for a human:** three cards for one outage.

## Direction: who depends on whom

The terms are easy to mix up, so the catalog should never rely on an arrow
alone. This proposal uses one convention throughout:

- **"A depends on B"** means A calls or uses B and fails when B fails.
  Example: lab-api depends on lab-postgres.
- **Arrow:** `A → B` points from the dependent to its dependency
  ("A uses B"). It is the direction of calls, not of failure.
- **Upstream / downstream:** B (the dependency, the provider) is
  **upstream**; A (the dependent, the consumer) is **downstream**. This
  matches proxy terminology (Envoy and NGINX call the backend the
  "upstream") and data pipelines (data flows from upstream sources). It also
  matches the existing prompt ("the health of the service's upstream
  dependencies, for example its database").
- **Failure propagates downstream, against the arrow:** when lab-postgres
  fails, lab-api (which depends on it) alerts afterwards. The root cause is
  usually the **most upstream** failing service in the group.

The catalog stores it explicitly as a `depends_on` list on the dependent
service, so nobody has to remember the arrow direction:

```yaml
- service_id: lab-api
  depends_on: [lab-postgres, lab-redis, lab-api-config]
```

### The lab's dependencies

| Service (downstream) | `depends_on` (upstream) | Why |
|---|---|---|
| lab-api | lab-postgres, lab-redis, lab-api-config | reads items from PostgreSQL, caches in Redis, runs with its runtime config |
| persist-demo-consumer | lab-kafka, lab-postgres | consumes from the broker, persists to the database |
| demo-producer | lab-kafka | produces to the broker |
| lab-loadgen | lab-api | a client that sends requests to lab-api |
| lab-postgres, lab-redis, lab-kafka, lab-api-config | - | no registered dependencies |

Two causal links are not plain dependencies:

- **lab-cpu-load** runs inside lab-api and competes for its CPU. Record it as
  `co_located_with: [lab-api]`, which counts as related for grouping but has
  no upstream/downstream direction.
- **lab-loadgen** causes trouble *downstream to upstream*: a traffic surge
  from the client overloads lab-api. Grouping must therefore treat the graph
  as undirected ("related"). Direction is only used to pick the most likely
  primary, never to decide whether two incidents belong together.

## Design

Keep one incident per service (dedup keys, state machines and audit stay as
they are) and add an **incident group** on top, instead of merging rows.

1. **Catalog (c5):** `depends_on` and `co_located_with`, validated: only
   registered services, no cycles, no self-reference.
2. **Grouping at ingestion:** when a new incident opens on service S, and an
   active incident on a service related to S (directly, in either direction)
   was opened within the grouping window, the new incident joins that
   incident's group. Otherwise it starts a new group. Window: **3 minutes**.
   In r2 related incidents opened 17-35 s apart, and the window must stay
   well below the gap between unrelated incidents.
3. **Primary incident:** the most upstream member of the group. When a more
   upstream incident joins later (e.g. lab-api alerts before lab-postgres),
   it becomes the primary.
4. **Settle before investigating:** the investigation job for a new group
   waits a short settle time (**60 s**) so related alerts can join, like
   Alertmanager's `group_wait`. Only the **primary** is investigated. Its
   prompt lists the other group members and their alerts as context, which
   tells it which services are failing downstream.
5. **Members follow the primary:** non-primary incidents get the state
   `GROUPED` (a new state that points to the primary). They get no
   investigation and no proposal of their own. When the primary is
   resolved, the verifier also checks the members' alerts. Members still
   firing afterwards are split out and investigated on their own.
6. **Console and chat:** one card per group showing the primary and its
   members; a member's page links to the primary.

### Data model

`incidents.group_id` (nullable) and `incident_groups(id, primary_incident_id,
opened_at, window_ends_at)`, plus a migration. Grouping and re-primarying
happen in the ingestion transaction, under the same row locks as today's
dedup.

## Effect and risks

- **Expected:** one investigation per fault instead of 2-3 (fewer tokens and
  repairs). No duplicate proposals, and no conflicting diagnoses across
  members of one fault. B-F02, B-F09 and B-F12 become one diagnosis each.
- **Risk: two unrelated faults in related services within 3 minutes** get
  grouped. Mitigation: a member that keeps firing after the primary
  recovers is split out (step 5), and a human can ungroup in the console.
- **Risk: delay.** Detection to diagnosis gets about 60 s longer (the settle
  time). In r2, diagnosis took a median of 47 s after the incident opened.
- **Benchmark grading:** grading 2.1 looks at every incident of a trial. With
  grouping, members have no diagnosis of their own, so grading must judge
  the group by its primary's diagnosis. That is a grading change and needs
  your approval and a new grading version (2.2), so it is not comparable 1:1
  with r1/r2.

## Effort and phases

| Phase | What | Effort |
|---|---|---|
| 1 | Catalog `depends_on`/`co_located_with` and validation; the investigation prompt lists related open incidents and the dependency edges as context (no change to incident lifecycle) | about half a day |
| 2 | Incident groups, primary selection, settle time, `GROUPED` state, only the primary investigated, verifier follow-up, migration | about 1.5 days |
| 3 | Console group cards, manual ungroup, grading 2.2 (judge groups by the primary) | about 1 day |

Each phase gets its own ADR section, tests and `make check`.

## Recommendation and decisions

- **Recommendation:** phase 1 now. It is cheap, changes no lifecycle, and
  already helps the model name the upstream component. Then phase 2, after
  you confirm the design.
- **Decisions needed:**
  1. The direction convention above (`depends_on` on the dependent, upstream
     = dependency). Is that how you want to read it?
  2. Group (keep incidents, add a primary) as proposed, or hard-merge into one
     incident?
  3. Grouping window 3 minutes and settle time 60 s: OK as lab values?
  4. Grading 2.2 for grouped incidents (phase 3): yes or no.
