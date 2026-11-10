import { describe, expect, it } from "vitest";

import { actionIsLive, incidentIsLive, listIsLive, runIsLive } from "./refresh";

describe("incidentIsLive", () => {
  it("polls while the incident or one of its actions can still change", () => {
    expect(incidentIsLive({ state: "INVESTIGATING" })).toBe(true);
    expect(incidentIsLive({ state: "RESOLVED" })).toBe(false);
    expect(incidentIsLive({ state: "ESCALATED" }, [{ state: "RECONCILING" }])).toBe(true);
    expect(incidentIsLive({ state: "ESCALATED" }, [{ state: "PENDING_APPROVAL" }])).toBe(false);
  });
});

describe("actionIsLive", () => {
  it("does not poll a pending approval", () => {
    expect(actionIsLive({ state: "PENDING_APPROVAL", verifications: [] })).toBe(false);
  });

  it("polls in-flight and unverified actions only", () => {
    expect(actionIsLive({ state: "RECONCILING", verifications: [] })).toBe(true);
    expect(actionIsLive({ state: "EXECUTING", verifications: [] })).toBe(true);
    expect(actionIsLive({ state: "SUCCEEDED", verifications: [] })).toBe(true);
    const verified = { id: "v", outcome: "PASSED", checks: [], window_start: "", window_end: "" };
    expect(actionIsLive({ state: "SUCCEEDED", verifications: [verified] })).toBe(false);
    expect(actionIsLive({ state: "REJECTED", verifications: [] })).toBe(false);
  });
});

describe("runIsLive and listIsLive", () => {
  it("polls only while work is running", () => {
    expect(runIsLive({ state: "RUNNING" })).toBe(true);
    expect(runIsLive({ state: "COMPLETED" })).toBe(false);
    expect(listIsLive([{ state: "RESOLVED" }, { state: "AWAITING_APPROVAL" }])).toBe(false);
    expect(listIsLive([{ state: "RESOLVED" }, { state: "VERIFYING" }])).toBe(true);
  });
});
