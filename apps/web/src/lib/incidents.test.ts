import { describe, expect, it } from "vitest";

import { fetchIncident, fetchIncidents, isIncidentDetail, isIncidentId } from "./incidents";

const ID = `inc_${"a".repeat(32)}`;

const summary = {
  id: ID,
  dedup_key: "fault-lab:lab-api:lab_api_latency_p95_high",
  service_id: "lab-api",
  environment: "fault-lab",
  severity: "warning",
  state: "RECEIVED",
  version: 1,
  created_at: "2026-11-09T12:00:00Z",
  updated_at: "2026-11-09T12:00:00Z",
};

const detail = {
  ...summary,
  alert_events: [
    {
      id: `alert_${"b".repeat(32)}`,
      status: "firing",
      alert_name: "lab_api_latency_p95_high",
      fingerprint: "0011aabb0011aabb",
      starts_at: "2026-11-09T11:58:00+00:00",
      received_at: "2026-11-09T12:00:00Z",
      labels: { severity: "warning" },
      annotations: {},
    },
  ],
  timeline: [{ at: "2026-11-09T12:00:00Z", kind: "alert", summary: "firing", reference_id: "x" }],
};

function respond(status: number, body: unknown, seen?: string[]): typeof fetch {
  return (async (input: RequestInfo | URL) => {
    seen?.push(String(input));
    return new Response(JSON.stringify(body), { status });
  }) as typeof fetch;
}

describe("fetchIncidents", () => {
  it("accepts a valid list", async () => {
    const result = await fetchIncidents("http://api", respond(200, [summary]));
    expect(result).toEqual({ ok: true, data: [summary] });
  });

  it("rejects unknown states", async () => {
    const result = await fetchIncidents("http://api", respond(200, [{ ...summary, state: "HACKED" }]));
    expect(result.ok).toBe(false);
  });

  it("surfaces HTTP errors", async () => {
    expect(await fetchIncidents("http://api", respond(401, {}))).toEqual({
      ok: false,
      error: "HTTP 401",
    });
  });
});

describe("fetchIncident", () => {
  it("returns a validated detail", async () => {
    const seen: string[] = [];
    const result = await fetchIncident("http://api", ID, respond(200, detail, seen));
    expect(result.ok).toBe(true);
    expect(seen).toEqual([`http://api/api/v1/incidents/${ID}`]);
  });

  it("refuses ids that could change the request path", async () => {
    const seen: string[] = [];
    const result = await fetchIncident("http://api", "../../admin", respond(200, detail, seen));
    expect(result).toEqual({ ok: false, error: "invalid incident id" });
    expect(seen).toEqual([]);
  });

  it("rejects malformed alert events", () => {
    expect(isIncidentDetail({ ...detail, alert_events: [{ id: 1 }] })).toBe(false);
  });
});

describe("isIncidentId", () => {
  it("matches only canonical incident ids", () => {
    expect(isIncidentId(ID)).toBe(true);
    expect(isIncidentId("inc_123")).toBe(false);
  });
});
