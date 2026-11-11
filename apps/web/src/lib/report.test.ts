import { describe, expect, it } from "vitest";

import type { ActionDetail } from "./actions";
import type { IncidentDetail } from "./incidents";
import { buildReport, reportDurations, reportMarkdown } from "./report";

const incident = (over: Partial<IncidentDetail> = {}): IncidentDetail => ({
  id: "inc_1",
  dedup_key: "k",
  service_id: "lab-consumer",
  environment: "fault-lab",
  severity: "warning",
  state: "RESOLVED",
  version: 9,
  created_at: "2026-11-09T12:00:00Z",
  updated_at: "2026-11-09T12:20:00Z",
  actions: [],
  escalation_reason: null,
  alert_events: [],
  timeline: [],
  runs: [
    {
      id: "run_1",
      incident_id: "inc_1",
      state: "COMPLETED",
      model_id: null,
      error_code: null,
      step_count: 2,
      tool_call_count: 2,
      input_tokens: 0,
      output_tokens: 0,
      started_at: "2026-11-09T12:00:05Z",
      finished_at: "2026-11-09T12:00:45Z",
    },
  ],
  diagnosis: {
    diagnosis: {
      root_cause_id: "consumer_paused",
      affected_component: "c",
      confidence_level: "high",
      summary: "",
      supporting_evidence_ids: [],
      contradicting_evidence_ids: [],
      unresolved_questions: [],
    },
    proposed_action: null,
    needs_human_review: false,
  },
  ...over,
});

const action = (outcome: string, passed: boolean): ActionDetail => ({
  id: "act_1",
  incident_id: "inc_1",
  state: "SUCCEEDED",
  version: 6,
  reason_code: null,
  action_hash: "sha256:x",
  policy_version: "p1",
  expires_at: "2026-11-09T12:15:00Z",
  envelope: { action_type: "resume_test_consumer", target_id: "lab-consumer" },
  approval: null,
  decisions: [
    {
      approver_id: "amy",
      decision: "APPROVED",
      action_hash: "sha256:x",
      reason: null,
      decided_at: "2026-11-09T12:03:00Z",
    },
  ],
  operations: [
    {
      provider: "fault-lab",
      provider_operation_id: "op",
      status: "COMPLETED",
      receipt: null,
      updated_at: "2026-11-09T12:03:10Z",
    },
  ],
  verifications: [
    {
      id: "ver_1",
      outcome,
      checks: [{ check_id: "lag_below_threshold", passed, observed: "48210" }],
      window_start: "2026-11-09T12:04:00Z",
      window_end: "2026-11-09T12:09:00Z",
    },
  ],
});

describe("buildReport", () => {
  it("summarises a resolved incident with milestones relative to opening", () => {
    const report = buildReport(incident(), [action("PASSED", true)]);
    expect(report.headline).toContain("Resolved (consumer_paused) by resume_test_consumer");
    expect(report.tone).toBe("success");
    expect(report.final).toBe(true);
    expect(report.milestones.map((m) => m.sinceOpen)).toEqual([
      "+<1 s",
      "+45 s",
      "+3 min",
      "+3 min 10 s",
      "+9 min",
      "+20 min",
    ]);
    expect(report.actions[0]).toMatchObject({
      approver: "amy",
      verification: "PASSED",
      failedChecks: [],
    });
  });

  it("explains a verification failure and lists the failed checks", () => {
    const report = buildReport(incident({ state: "ESCALATED" }), [
      action("VERIFICATION_FAILED", false),
    ]);
    expect(report.headline).toContain(
      "independent verification found the service had not recovered",
    );
    expect(report.tone).toBe("danger");
    expect(report.actions[0].failedChecks).toEqual(["lag_below_threshold: 48210"]);
  });

  it("marks an open incident's report as provisional", () => {
    const report = buildReport(incident({ state: "EXECUTING", diagnosis: null }), []);
    expect(report.final).toBe(false);
    expect(report.headline).toContain("provisional");
    expect(report.milestones[1]).toEqual({
      label: "Diagnosis returned",
      at: null,
      sinceOpen: null,
    });
  });
});


describe("reportMarkdown", () => {
  it("renders outcome, milestones and follow-ups", () => {
    const incident = {
      id: "inc_1", dedup_key: "k", service_id: "svc", environment: "lab", severity: "critical",
      state: "RESOLVED", version: 3, created_at: "2026-11-11T08:00:00Z", updated_at: "2026-11-11T08:05:00Z",
      escalation_reason: null, alert_events: [], timeline: [], runs: [], actions: [],
      diagnosis: {
        diagnosis: {
          root_cause_id: "consumer_paused", affected_component: "c", confidence_level: "high", summary: "Paused.",
          supporting_evidence_ids: [], contradicting_evidence_ids: [], unresolved_questions: ["Why paused?"],
        },
        proposed_action: null, needs_human_review: false,
      },
    } as unknown as Parameters<typeof reportMarkdown>[0];
    const report = {
      headline: "Resolved.", tone: "success" as const, final: true,
      milestones: [{ label: "Verification finished", at: "2026-11-11T08:04:00Z", sinceOpen: "+4 min" }],
      actions: [],
    };
    const md = reportMarkdown(incident, report, "Lag high", [{ role: "supports", sentence: "Lag grew." }]);
    expect(md).toContain("# Lag high\n");
    expect(md).toContain("| Verification finished | 2026-11-11T08:04:00Z | +4 min |");
    expect(md).toContain("- ✓ Lag grew.");
    expect(md).toContain("- [ ] Why paused?");
    expect(reportDurations(incident, report).toRecovery).toBe(240_000);
  });
});
