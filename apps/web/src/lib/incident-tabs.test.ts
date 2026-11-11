import { describe, expect, it } from "vitest";
import { incidentTabHref, parseIncidentTab } from "./incident-tabs";

describe("local incident navigation", () => {
  it("restores supported deep links and falls back to Story", () => {
    expect(parseIncidentTab("evidence")).toBe("evidence");
    expect(parseIncidentTab(null)).toBe("story");
    expect(parseIncidentTab("unknown")).toBe("story");
  });
  it("pins the selected incident and keeps evidence anchors", () => {
    expect(incidentTabHref("inc_123456", "story")).toBe("/incidents/inc_123456");
    expect(incidentTabHref("inc_123456", "evidence", "#ev_123456")).toBe("/incidents/inc_123456?tab=evidence#ev_123456");
  });
});
