import { describe, expect, it } from "vitest";

import { buildTimeline, formatDuration } from "./timeline";

describe("formatDuration", () => {
  it("formats compact durations", () => {
    expect(formatDuration(200)).toBe("<1 s");
    expect(formatDuration(42_000)).toBe("42 s");
    expect(formatDuration(125_000)).toBe("2 min 5 s");
    expect(formatDuration(120_000)).toBe("2 min");
    expect(formatDuration(3_780_000)).toBe("1 h 3 min");
    expect(formatDuration(-5)).toBe("<1 s");
  });
});

describe("buildTimeline", () => {
  const entries = [
    { at: "2026-11-09T12:00:41Z", kind: "tool", summary: "query_metrics DENIED", reference_id: "s1" },
    { at: "2026-11-09T12:00:00Z", kind: "alert", summary: "firing delivery (ab12)", reference_id: "a1" },
    { at: "2026-11-09T12:00:02Z", kind: "audit", summary: "incident_created: RECEIVED", reference_id: "u1" },
    {
      at: "2026-11-09T12:05:00Z",
      kind: "audit",
      summary: "verification_completed: VERIFICATION_FAILED",
      reference_id: "u2",
    },
    { at: "2026-11-09T12:06:00Z", kind: "audit", summary: "custom_event:", reference_id: "u3" },
  ];

  it("sorts entries and adds offsets from the first one", () => {
    const items = buildTimeline(entries);
    expect(items.map((i) => i.offset)).toEqual(["", "+2 s", "+41 s", "+5 min", "+6 min"]);
  });

  it("labels and tones each kind", () => {
    const [alert, created, tool, verification, custom] = buildTimeline(entries);
    expect(alert).toMatchObject({ category: "alert", title: "Alert firing", tone: "danger" });
    expect(created).toMatchObject({ title: "Incident opened", detail: "RECEIVED", tone: "progress" });
    expect(tool).toMatchObject({ category: "tool", title: "Tool query_metrics", tone: "danger" });
    expect(verification).toMatchObject({ title: "Verification completed", tone: "danger" });
    expect(custom).toMatchObject({ title: "Custom event", detail: "", tone: "neutral" });
  });

  it("handles an empty timeline", () => {
    expect(buildTimeline([])).toEqual([]);
  });
});
