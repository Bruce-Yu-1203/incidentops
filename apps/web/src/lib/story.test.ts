import { describe, expect, it } from "vitest";

import { buildStory, compactSummary, recoveryResult } from "./story";

const diagnosis = {
  diagnosis: {
    root_cause_id: "consumer_paused",
    affected_component: "c",
    confidence_level: "high",
    summary: "The consumer stopped reading.",
    supporting_evidence_ids: [],
    contradicting_evidence_ids: [],
    unresolved_questions: [],
  },
  proposed_action: null,
  needs_human_review: false,
};
const pending = { id: "act_1", action_type: "a", target_id: "t", state: "PENDING_APPROVAL", version: 1, expires_at: "" };

describe("buildStory", () => {
  it("marks the proposal as the current step while awaiting approval", () => {
    const story = buildStory(
      { state: "AWAITING_APPROVAL", diagnosis, actions: [pending], title: null, dedup_key: "k" },
      null,
    );
    expect(story.headline).toBe("consumer paused · c");
    expect(story.summary).toBe("The consumer stopped reading.");
    expect(story.steps.map((s) => s.status)).toEqual(["done", "done", "current", "upcoming"]);
  });

  it("shows a red final step when verification failed", () => {
    const action = {
      state: "SUCCEEDED",
      verifications: [{ id: "v", outcome: "VERIFICATION_FAILED", checks: [], window_start: "", window_end: "" }],
    };
    const story = buildStory(
      { state: "ESCALATED", diagnosis, actions: [{ ...pending, state: "SUCCEEDED" }], title: null, dedup_key: "k" },
      action,
    );
    expect(story.worked).toBe("no");
    expect(story.steps[3].status).toBe("failed");
  });

  it("keeps the why step current while investigating", () => {
    const story = buildStory(
      { state: "INVESTIGATING", diagnosis: null, actions: [], title: null, dedup_key: "k" },
      null,
    );
    expect(story.steps[1].status).toBe("current");
    expect(story.summary).toMatch(/still looking/);
  });
});

describe("recoveryResult", () => {
  it("treats resolved incidents as recovered and no action as none", () => {
    expect(recoveryResult({ state: "RESOLVED" }, null)).toBe("yes");
    expect(recoveryResult({ state: "DIAGNOSED_ONLY" }, null)).toBe("none");
  });
});


describe("compact incident summary", () => {
  it("bounds a long model response without using it as a headline", () => {
    const full = "Requests began failing after the database acquired an exclusive lock. " + "Detailed evidence and telemetry readings. ".repeat(80);
    const report = { ...diagnosis, diagnosis: { ...diagnosis.diagnosis, summary: full } };
    const story = buildStory({ state: "AWAITING_APPROVAL", diagnosis: report, actions: [pending], title: "latency", dedup_key: "k" }, null);
    expect(story.headline).toBe("consumer paused · c");
    expect(story.summary.length).toBeLessThanOrEqual(220);
    expect(story.summary).toContain("exclusive lock.");
    expect(report.diagnosis.summary).toBe(full);
  });
  it("bounds a long single sentence and text without spaces", () => {
    expect(compactSummary("word ".repeat(300)).length).toBeLessThanOrEqual(220);
    expect(compactSummary("长".repeat(600)).length).toBeLessThanOrEqual(220);
    expect(compactSummary("  Short\nsummary. ")).toBe("Short summary.");
  });
});
