# ADR-031: Prompt investigation-v12, dependency and group context, and G3

- **Status:** Accepted (Amy approved the recommendations on 2026-11-16)
- **Relates to:** ADR-029 (affected_component rule), ADR-030 (groups);
  `docs/proposals/2026-11-15-grouping-scenarios-grading-prompt.md` (section 4),
  `docs/proposals/2026-11-15-prompt-v12-draft.md` (the full text)

## Context

ADR-030 put the group members into the primary's context as data, with no
words on how to use them. The catalog context did not show the dependency
edges, so "check the upstream dependencies" had to be guessed from service
descriptions. Guard G3 (the primary names members it cannot explain) needed a
field to say so.

## Decision

1. **`PROMPT_VERSION = "investigation-v12"`** with the system prompt of the
   draft: two new blocks, `<service_dependencies>` (direction convention,
   co-location, surges can go client to dependency) and `<incident_group>`
   (related incidents are leads, explain each or list it; `earlier_diagnosis`
   is a lead; a `split_from_group` incident looks for a second, independent
   cause). The "every incident caused by one fault gets the same
   affected_component" sentence moved from `<rules>` into `<incident_group>`;
   `<investigation_method>` points the dependency check at `depends_on`. The
   other blocks are unchanged.
2. **Catalog context** entries carry `depends_on` and `co_located_with`.
   `incident_group.related_incidents` entries carry `incident_id`.
3. **G3:** `DiagnosisReport.unexplained_incident_ids` is an optional top-level
   list (lifted from inside `diagnosis` like the other top-level fields,
   ADR-028). The tool schema suggests the `inc_<32 hex>` pattern and at most
   10 items, but validation never rejects a report over it. In the diagnosis
   transaction, before routing (a terminal primary would otherwise take its
   members along), the harness splits each listed ID that is a current,
   unsplit GROUPED member of the group this incident is the primary of
   (`GROUPED` → `RECEIVED`, investigation job, reason
   `unexplained_by_primary`, note "the group primary's diagnosis does not
   explain this incident"). Other IDs are dropped. One audit event
   `unexplained_members_reviewed` records "N split, M dropped". A replaced
   primary or a re-investigation never splits members.
4. **Submit description** says there are three top-level fields plus the
   optional `unexplained_incident_ids`.

## Consequences

- Results with v12 are not 1:1 comparable with r1/r2 (v11). The next run also
  uses grading 2.2 (ADR-032), so comparability breaks once.
- Incidents without a group see only the catalog edges and the
  `<service_dependencies>` text; `<incident_group>` has no data to act on.
- The agent role already holds the privileges a split needs (incident update,
  audit and outbox insert).
