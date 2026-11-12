import { describe, expect, it } from "vitest";

import { METRIC_GROUPS, chartGeometry, formatValue, panelForService, parseRange } from "./metrics";

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
    expect(panelForService("lab-kafka")).toBe("kafka_brokers");
    expect(panelForService("demo-producer")).toBeNull();
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

it("keeps negative growth visible and breaks paths across missing samples", () => {
  const g = chartGeometry({ series: [{ name: "growth", points: [[0, -2], [5, 3], [60, 2]] }], threshold: 1 }, 0, 60_000, [], 600, 100, 5);
  expect(g.min).toBe(-2);
  expect(g.paths[0].d.match(/M/g)).toHaveLength(2);
  expect(g.paths[0].d).toContain("M600.0,");
});

it("does not shade incidents outside the requested time window", () => {
  const g = chartGeometry({ series: [], threshold: null }, 60_000, 120_000,
    [{ incident_id: "old", title: null, service_id: "lab-api", start: new Date(0).toISOString(), end: new Date(30_000).toISOString() }]);
  expect(g.bands).toEqual([]);
});

it("filters incident overlays by the panel service and identifies stale or empty data", async () => {
  const { panelWindows, panelHealth } = await import("./metrics");
  const windows = [
    { incident_id: "api", title: null, service_id: "lab-api", start: new Date(0).toISOString(), end: null },
    { incident_id: "redis", title: null, service_id: "lab-redis", start: new Date(0).toISOString(), end: null },
  ];
  expect(panelWindows("api_cpu", windows).map((w) => w.incident_id)).toEqual(["api"]);
  const panel = { panel_id: "api_cpu", title: "CPU", unit: "cores", latest: 0.2, threshold: 0.6, status: "ok" as const, series: [{ name: "cpu", points: [[0, 0.2]] as [number, number][] }] };
  expect(panelHealth(panel, 120_000, 5)).toBe("Stale data");
  expect(panelHealth({ ...panel, series: [] }, 120_000, 5)).toBe("No data");
  expect(formatValue(0.6, "cores")).toBe("0.60 cores");
  expect(formatValue(-2000000, "bytes/s")).toBe("-2.00 MB/s");
});

it("merges overlapping incident bands so opacity does not accumulate", () => {
  const g = chartGeometry({ series: [], threshold: null }, 0, 100_000, [
    { incident_id: "a", title: null, service_id: "lab-api", start: new Date(0).toISOString(), end: new Date(60_000).toISOString() },
    { incident_id: "b", title: null, service_id: "lab-api", start: new Date(30_000).toISOString(), end: new Date(90_000).toISOString() },
  ], 100, 100);
  expect(g.bands).toEqual([{ id: "a", x: 0, width: 90 }]);
  expect(g.max).toBeGreaterThan(1);
});


describe("panels for the new root causes", () => {
  it("chooses the panel that explains each new alert", () => {
    expect(panelForService("lab-postgres", "lab_postgres_down")).toBe("pg_up");
    expect(panelForService("lab-postgres", "lab_postgres_connections_near_limit")).toBe("pg_pool_usage");
    expect(panelForService("lab-postgres")).toBe("pg_locks");
    expect(panelForService("lab-api", "lab_api_request_rate_surge")).toBe("api_request_rate");
    expect(panelForService("persist-demo-consumer", "lab_consumer_processing_slow")).toBe("produce_consume");
  });

  it("groups the new panels on the Metrics page", () => {
    const ids = METRIC_GROUPS.flatMap((g) => [...g.ids]);
    for (const id of ["api_request_rate", "api_memory_growth", "kafka_brokers", "pg_up", "pg_pool_usage"]) {
      expect(ids).toContain(id);
    }
  });

  it("formats request rates and broker counts", () => {
    expect(formatValue(61.04, "requests/s")).toBe("61.0 req/s");
    expect(formatValue(1, "brokers")).toBe("1 broker");
    expect(formatValue(0, "brokers")).toBe("0 brokers");
  });
});
