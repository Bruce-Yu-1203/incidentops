import { describe, expect, it } from "vitest";

import { actionLifecycle, incidentLifecycle, VERIFY_STEP, visitedStates } from "./lifecycle";

const statuses = (steps: { label: string; status: string }[]) =>
  steps.map((s) => `${s.label}:${s.status}`);

describe("incidentLifecycle", () => {
  it("marks earlier steps done and the current one as current", () => {
    const { steps, exits } = incidentLifecycle("AWAITING_APPROVAL");
    expect(statuses(steps).slice(3, 6)).toEqual([
      "DIAGNOSED:done",
      "AWAITING_APPROVAL:current",
      "EXECUTING:upcoming",
    ]);
    expect(steps[4].tone).toBe("attention");
    expect(exits).toEqual(["ESCALATED", "CANCELLED"]);
  });

  it("shows RESOLVED as a fully completed success path", () => {
    const { steps, exits } = incidentLifecycle("RESOLVED");
    expect(steps.every((s) => s.status === "done" && s.tone === "success")).toBe(true);
    expect(exits).toEqual([]);
  });

  it("draws an exit where the history says it happened", () => {
    const { steps } = incidentLifecycle("ESCALATED", ["RECEIVED", "EXECUTING", "VERIFYING"]);
    const labels = steps.map((s) => s.label);
    expect(labels.indexOf("ESCALATED")).toBe(labels.indexOf("VERIFYING") + 1);
    expect(steps.find((s) => s.label === "ESCALATED")).toMatchObject({
      status: "current",
      tone: "danger",
    });
    expect(steps.find((s) => s.label === "RESOLVED")?.status).toBe("skipped");
  });

  it("places known off-path states without history", () => {
    const labels = incidentLifecycle("DIAGNOSED_ONLY").steps.map((s) => s.label);
    expect(labels.indexOf("DIAGNOSED_ONLY")).toBe(labels.indexOf("DIAGNOSED") + 1);
  });
});

describe("actionLifecycle", () => {
  it("adds independent verification after SUCCEEDED", () => {
    const pending = actionLifecycle("SUCCEEDED", null).steps;
    expect(pending.at(-1)).toMatchObject({ label: VERIFY_STEP, status: "current" });
    const failed = actionLifecycle("SUCCEEDED", "VERIFICATION_FAILED").steps;
    expect(failed.at(-2)).toMatchObject({ label: "SUCCEEDED", status: "done", tone: "success" });
    expect(failed.at(-1)).toMatchObject({ label: "VERIFICATION_FAILED", tone: "danger" });
    const passed = actionLifecycle("SUCCEEDED", "PASSED").steps;
    expect(passed.at(-1)).toMatchObject({ label: VERIFY_STEP, status: "done", tone: "success" });
  });

  it("shows RECONCILING as an unknown branch after EXECUTING", () => {
    const { steps, exits } = actionLifecycle("RECONCILING", null);
    expect(statuses(steps).slice(4, 6)).toEqual(["EXECUTING:done", "RECONCILING:current"]);
    expect(steps[5].tone).toBe("unknown");
    expect(exits).toEqual(["SUCCEEDED", "FAILED", "ESCALATED"]);
    expect(steps.at(-1)?.status).toBe("skipped");
  });

  it("places a cancellation before or after approval", () => {
    const before = actionLifecycle("CANCELLED", null, false).steps.map((s) => s.label);
    expect(before.indexOf("CANCELLED")).toBe(2);
    const after = actionLifecycle("CANCELLED", null, true).steps.map((s) => s.label);
    expect(after.indexOf("CANCELLED")).toBe(3);
  });

  it("leaves verification upcoming while the action is in flight", () => {
    expect(actionLifecycle("QUEUED", null).steps.at(-1)?.status).toBe("upcoming");
  });
});

describe("visitedStates", () => {
  it("reads incident states from audit summaries", () => {
    expect(visitedStates(["incident_created: RECEIVED", "login", "x: VERIFYING"])).toEqual([
      "RECEIVED",
      "VERIFYING",
    ]);
  });
});

describe("positionKnown", () => {
  it("is false only for an exit with no history to place it", () => {
    expect(incidentLifecycle("ESCALATED").positionKnown).toBe(false);
    expect(incidentLifecycle("ESCALATED", ["VERIFYING"]).positionKnown).toBe(true);
    expect(incidentLifecycle("EXECUTING").positionKnown).toBe(true);
    expect(incidentLifecycle("DIAGNOSED_ONLY").positionKnown).toBe(true);
  });

  it("keeps earlier action steps neutral-progress when only SUCCEEDED is green", () => {
    const steps = actionLifecycle("SUCCEEDED", "VERIFICATION_FAILED").steps;
    expect(steps[0].tone).toBe("progress");
    expect(steps.at(-2)?.tone).toBe("success");
  });
});
