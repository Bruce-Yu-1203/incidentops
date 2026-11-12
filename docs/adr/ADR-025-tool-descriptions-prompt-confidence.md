# ADR-025: Tool descriptions, investigation method and confidence rules

- **Status:** Accepted
- **Relates to:** ADR-024, architecture §6–§7, benchmark.md §6

## Context

Tool descriptions said only what a tool is, parameters had no descriptions,
the system prompt was a flat rule list, and `confidence_level` had no
definition, so models (and the B0 rules) filled it in by feel. Anthropic's
tool-use guidance recommends detailed descriptions that say when to call a
tool, and warns that rigid step-by-step scripts for judgement tasks tend to
lower quality on current models; goals, constraints and verification work
better, with strict order kept only where order really matters.

## Decision

1. **Tool descriptions (ToolSpec version 2).** Each read tool states its
   purpose, when to use it, what it returns, how to read the result and its
   limits, in three to five sentences. Interpretation is generic domain
   knowledge, never a scenario answer, and no lab threshold is written into a
   description. Every input parameter has a `Field(description=...)`, which
   flows into the input schema. `query_metrics` lists its templates grouped by
   the question they answer (`templates.QUESTIONS`; a test keeps every
   template in exactly one group). Results explain themselves where it is
   cheap: `get_postgres_health` reports connection usage as a share of
   `max_connections`, and traffic is read through
   `api_request_rate_vs_baseline` (ADR-024).
2. **Prompt `investigation-v7`.** The system prompt has three sections:
   `<rules>` (unchanged rules), `<investigation_method>` and
   `<confidence_rubric>`. The method lists the evidence a diagnosis should
   cover, each with its reason, and a stop condition, with no fixed order:
   symptom and onset on the alerting service; nearby releases and config
   changes; upstream dependency health; at least one look-alike check phrased
   generically (stopped vs slow vs unreachable; blocked vs out of slots vs
   down; bad release vs bad config vs code error; more traffic vs less
   capacity). The rubric is repeated as the `confidence_level` schema
   description.
3. **Confidence rules.**
   - Rubric: *high* = direct mechanism evidence on the named component,
     consistent onset, a look-alike check and no unexplained contradiction;
     *medium* = component clear, mechanism indirect or no look-alike check;
     *low* = symptoms only or conflicting evidence.
   - Checked mechanically when a report is validated
     (`incidentops/agent/confidence.py`); a violation is returned to the
     model once with the reason, like any invalid report:
     - `insufficient_evidence` must be `low`;
     - `low` may not carry a `proposed_action`;
     - `high` must cite readings from at least two different tools or metric
       templates, at least one of which is not a symptom reading (symptom
       templates: API error ratio, latency and request rate, consumer lag,
       service logs and traces).
   - Policy `p5` adds `minimum_action_confidence: medium`; a proposal from a
     lower-confidence diagnosis is denied as `confidence_below_minimum`
     (defence in depth behind validation; unknown levels deny). Medium may
     still lead to an approval request, because a human approves every action
     anyway and the B0 rules diagnose several action cases at medium. The
     executor's re-check passes no confidence, as for the root cause
     (ADR-024).
   - What code cannot verify (whether a cited check really separates the
     look-alike, or whether contradictions are explained) stays prompt-level
     judgement.

The B0 rules already satisfy these rules unchanged (their unit tests run the
real validation). Prompt and tool changes have not been measured on an LLM:
A/B runs on B-F01..B-F13 and B-N01 (root-cause accuracy, tool calls, tokens)
are future work, as are the hierarchical root cause (category / component /
mechanism) and topology-based alert aggregation discussed with the owner.
