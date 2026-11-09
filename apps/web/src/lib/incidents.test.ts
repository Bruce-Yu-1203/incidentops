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

const run = {
  id: `run_${"c".repeat(32)}`,
  incident_id: ID,
  state: "COMPLETED",
  model_id: "scripted-test-model",
  prompt_version: "investigation-v1",
  error_code: null,
  step_count: 2,
  tool_call_count: 2,
  input_tokens: 0,
  output_tokens: 0,
  started_at: "2026-11-09T12:00:00Z",
  finished_at: "2026-11-09T12:01:00Z",
};

const report = {
  diagnosis: {
    root_cause_id: "consumer_paused",
    affected_component: "persist-demo-consumer",
    confidence_level: "high",
    summary: "Paused.",
    supporting_evidence_ids: [`ev_${"d".repeat(32)}`],
    contradicting_evidence_ids: [],
    unresolved_questions: [],
  },
  proposed_action: null,
  needs_human_review: true,
};

const detail = {
  ...summary,
  escalation_reason: null,
  actions: [],
  runs: [run],
  diagnosis: report,
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

describe("isIncidentDetail diagnosis and runs", () => {
  it("accepts an incident without diagnosis", () => {
    expect(isIncidentDetail({ ...detail, diagnosis: null, runs: [] })).toBe(true);
  });

  it("rejects malformed runs and reports", () => {
    expect(isIncidentDetail({ ...detail, runs: [{ id: 1 }] })).toBe(false);
    expect(isIncidentDetail({ ...detail, diagnosis: { diagnosis: {} } })).toBe(false);
  });
});
