import { describe, expect, it } from "vitest";

import { chartGeometry, formatValue, panelForService, parseRange } from "./metrics";

describe("chartGeometry", () => {
  it("scales points into the view box and keeps the threshold visible", () => {
    const g = chartGeometry(
      { series: [{ name: "p95", points: [[0, 0.1], [60, 0.3]] }], threshold: 0.5 },
      0,
      60_000,
      [{ incident_id: "inc", title: null, service_id: "lab-api", start: new Date(30_000).toISOString(), end: null }],
      600,
      100,
    );
    expect(g.paths[0].d.startsWith("M0.0,")).toBe(true);
    expect(g.paths[0].d).toContain("L600.0,");
    expect(g.thresholdY).toBeGreaterThan(0);
    expect(g.thresholdY).toBeLessThan(100);
    expect(g.bands).toEqual([{ x: 300, width: 300, id: "inc" }]);
  });
});

describe("formatValue", () => {
  it("formats by unit", () => {
    expect(formatValue(0.71, "seconds")).toBe("710 ms");
    expect(formatValue(0.006, "ratio")).toBe("0.6 %");
    expect(formatValue(41812, "messages")).toBe("41.8k");
    expect(formatValue(50e6, "bytes")).toBe("50.0 MB");
    expect(formatValue(null, "bytes")).toBe("no data");
  });
});

describe("helpers", () => {
  it("parses ranges and maps services to panels", () => {
    expect(parseRange("60")).toBe(60);
    expect(parseRange("7")).toBe(30);
    expect(panelForService("persist-demo-consumer")).toBe("consumer_lag");
    expect(panelForService("lab-api", "lab_api_error_ratio_high")).toBe("api_5xx");
    expect(panelForService("lab-kafka")).toBeNull();
  });
});

it("requests the incident replay endpoint without a moving minutes window", async () => {
  const { fetchIncidentMetrics } = await import("./metrics");
  let requested = "";
  const result = await fetchIncidentMetrics("http://api", "inc_123456", (async (url) => {
    requested = String(url);
    return new Response(JSON.stringify({ start: "2026-11-09T12:00:00Z", panels: [], incidents: [], markers: [], mode: "historical" }));
  }) as typeof fetch);
  expect(requested).toBe("http://api/api/v1/incidents/inc_123456/metrics");
  expect(result.ok).toBe(true);
});
