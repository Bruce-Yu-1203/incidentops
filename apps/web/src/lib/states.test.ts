import { describe, expect, it } from "vitest";

import { INCIDENT_STATES } from "./incidents";
import { isTerminal, toneOf } from "./states";

describe("toneOf", () => {
  it("keeps the two failure modes visually distinct", () => {
    expect(toneOf("action", "RECONCILING")).toBe("unknown");
    expect(toneOf("verification", "VERIFICATION_FAILED")).toBe("danger");
    expect(toneOf("action", "RECONCILING")).not.toBe(toneOf("verification", "VERIFICATION_FAILED"));
  });

  it("marks states that wait on a human as attention", () => {
    expect(toneOf("incident", "AWAITING_APPROVAL")).toBe("attention");
    expect(toneOf("action", "PENDING_APPROVAL")).toBe("attention");
    expect(toneOf("approval", "PENDING")).toBe("attention");
  });

  it("does not show an action success as an incident resolution", () => {
    expect(toneOf("action", "SUCCEEDED")).toBe("success");
    expect(toneOf("incident", "VERIFYING")).toBe("progress");
  });

  it("maps every incident state", () => {
    for (const state of INCIDENT_STATES) {
      expect(toneOf("incident", state)).toBeTypeOf("string");
    }
    expect(toneOf("incident", "RESOLVED")).toBe("success");
    expect(toneOf("incident", "ESCALATED")).toBe("danger");
  });

  it("is case-insensitive and falls back to neutral", () => {
    expect(toneOf("tool", "denied")).toBe("danger");
    expect(toneOf("action", "SOMETHING_NEW")).toBe("neutral");
  });
});

describe("isTerminal", () => {
  it("knows which states are final", () => {
    expect(isTerminal("incident", "RESOLVED")).toBe(true);
    expect(isTerminal("incident", "EXECUTING")).toBe(false);
    expect(isTerminal("action", "RECONCILING")).toBe(false);
    expect(isTerminal("action", "SUCCEEDED")).toBe(true);
    expect(isTerminal("run", "RUNNING")).toBe(false);
  });
});

describe("receipt tones", () => {
  it("shows an unknown receipt as unknown, not failed", () => {
    expect(toneOf("receipt", "UNKNOWN")).toBe("unknown");
    expect(toneOf("receipt", "COMPLETED")).toBe("success");
  });
});
