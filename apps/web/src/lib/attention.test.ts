import { describe, expect, it } from "vitest";

import { filterIncidents, formatAge, groupAttention, parseView } from "./attention";
import type { IncidentSummary } from "./incidents";

const incident = (
  id: string,
  state: IncidentSummary["state"],
  created_at: string,
): IncidentSummary => ({
  id,
  dedup_key: `key-${id}`,
  service_id: "lab-consumer",
  environment: "fault-lab",
  severity: "warning",
  state,
  version: 1,
  created_at,
  updated_at: created_at,
});

const incidents = [
  incident("b", "AWAITING_APPROVAL", "2026-11-09T12:10:00Z"),
  incident("a", "AWAITING_APPROVAL", "2026-11-09T12:00:00Z"),
  incident("c", "ESCALATED", "2026-11-09T12:00:00Z"),
  incident("d", "INVESTIGATING", "2026-11-09T12:00:00Z"),
  incident("e", "RESOLVED", "2026-11-09T12:00:00Z"),
  incident("f", "FAILED", "2026-11-09T12:00:00Z"),
];

describe("groupAttention", () => {
  it("puts each incident in the bucket for its state, oldest first", () => {
    const groups = groupAttention(incidents);
    expect(groups.awaiting_approval.map((i) => i.id)).toEqual(["a", "b"]);
    expect(groups.escalated.map((i) => i.id)).toEqual(["c"]);
    expect(groups.in_progress.map((i) => i.id)).toEqual(["d"]);
    expect(groups.failed.map((i) => i.id)).toEqual(["f"]);
  });
});

describe("filterIncidents", () => {
  it("keeps escalated incidents in the open view", () => {
    expect(filterIncidents(incidents, "open").map((i) => i.id)).toEqual(["b", "a", "c", "d"]);
    expect(filterIncidents(incidents, "closed").map((i) => i.id)).toEqual(["e", "f"]);
    expect(filterIncidents(incidents, "all")).toHaveLength(6);
  });

  it("defaults unknown views to open", () => {
    expect(parseView(undefined)).toBe("open");
    expect(parseView("bogus")).toBe("open");
    expect(parseView("closed")).toBe("closed");
  });
});

describe("formatAge", () => {
  const now = new Date("2026-11-09T12:00:00Z");
  it("formats compact ages", () => {
    expect(formatAge("2026-11-09T11:59:15Z", now)).toBe("45 s");
    expect(formatAge("2026-11-09T11:46:00Z", now)).toBe("14 min");
    expect(formatAge("2026-11-09T09:00:00Z", now)).toBe("3 h");
    expect(formatAge("2026-11-07T12:00:00Z", now)).toBe("2 d");
    expect(formatAge("2026-11-09T12:01:00Z", now)).toBe("0 s");
  });
});
