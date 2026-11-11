import { describe, expect, it } from "vitest";

import { buildStory, recoveryResult } from "./story";

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
    expect(story.headline).toBe("The consumer stopped reading.");
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
    expect(story.headline).toMatch(/still looking/);
  });
});

describe("recoveryResult", () => {
  it("treats resolved incidents as recovered and no action as none", () => {
    expect(recoveryResult({ state: "RESOLVED" }, null)).toBe("yes");
    expect(recoveryResult({ state: "DIAGNOSED_ONLY" }, null)).toBe("none");
  });
});
