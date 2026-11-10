import { describe, expect, it } from "vitest";

import { actionOutcome, sortChecks } from "./outcomes";

const verification = (outcome: string) => ({
  id: "ver_1",
  outcome,
  checks: [],
  window_start: "2026-11-09T12:00:00Z",
  window_end: "2026-11-09T12:05:00Z",
});

describe("actionOutcome", () => {
  it("separates an unknown write from a failed recovery", () => {
    const reconciling = actionOutcome({ state: "RECONCILING", verifications: [] });
    const failed = actionOutcome({
      state: "SUCCEEDED",
      verifications: [verification("VERIFICATION_FAILED")],
    });
    expect(reconciling).toMatchObject({ kind: "reconciling", tone: "unknown" });
    expect(reconciling?.body).toContain("will not send the write again");
    expect(failed).toMatchObject({ kind: "verification_failed", tone: "danger" });
  });

  it("uses the latest verification", () => {
    const outcome = actionOutcome({
      state: "SUCCEEDED",
      verifications: [verification("UNKNOWN"), verification("PASSED")],
    });
    expect(outcome?.kind).toBe("verified");
  });

  it("reports inconclusive verification as unknown, not failure", () => {
    expect(
      actionOutcome({ state: "SUCCEEDED", verifications: [verification("UNKNOWN")] })?.tone,
    ).toBe("unknown");
  });

  it("has nothing to say while the action is in flight", () => {
    expect(actionOutcome({ state: "QUEUED", verifications: [] })).toBeNull();
    expect(actionOutcome({ state: "SUCCEEDED", verifications: [] })).toBeNull();
  });
});

describe("sortChecks", () => {
  it("lists failed checks first, then unknown, then passed", () => {
    const checks = [
      { check_id: "a", passed: true, observed: "" },
      { check_id: "b", passed: null, observed: "" },
      { check_id: "c", passed: false, observed: "" },
      { check_id: "d", passed: false, observed: "" },
    ];
    expect(sortChecks(checks).map((c) => c.check_id)).toEqual(["c", "d", "b", "a"]);
  });
});
