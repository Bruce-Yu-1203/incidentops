import { describe, expect, it } from "vitest";

import { fetchRun, isRunId, safeSourceUrl } from "./investigations";

const RUN = `run_${"c".repeat(32)}`;

const detail = {
  id: RUN,
  incident_id: `inc_${"a".repeat(32)}`,
  state: "COMPLETED",
  model_id: "m",
  prompt_version: "investigation-v1",
  error_code: null,
  step_count: 1,
  tool_call_count: 1,
  input_tokens: 10,
  output_tokens: 5,
  started_at: "2026-11-09T12:00:00Z",
  finished_at: "2026-11-09T12:01:00Z",
  steps: [
    {
      id: "step_1",
      kind: "tool_call",
      status: "DENIED",
      started_at: "2026-11-09T12:00:00Z",
      finished_at: null,
      metadata: { tool: "run_shell" },
    },
  ],
  evidence: [
    {
      id: `ev_${"d".repeat(32)}`,
      source: "prometheus",
      template_id: "kafka_group_lag",
      parameters: {},
      window_start: "2026-11-09T11:50:00Z",
      window_end: "2026-11-09T12:00:00Z",
      observed_at: "2026-11-09T12:00:00Z",
      digest: `sha256:${"0".repeat(64)}`,
      summary: "lag 100 -> 1300",
      truncated: false,
      source_url: "http://127.0.0.1:3000/explore?x=1",
    },
  ],
  diagnosis: null,
};

const respond =
  (status: number, body: unknown): typeof fetch =>
  (async () => new Response(JSON.stringify(body), { status })) as typeof fetch;

describe("fetchRun", () => {
  it("validates a run detail", async () => {
    const result = await fetchRun("http://api", RUN, respond(200, detail));
    expect(result.ok).toBe(true);
  });

  it("rejects malformed evidence", async () => {
    const bad = { ...detail, evidence: [{ id: 3 }] };
    expect((await fetchRun("http://api", RUN, respond(200, bad))).ok).toBe(false);
  });

  it("refuses non-canonical run ids", async () => {
    expect(await fetchRun("http://api", "../x", respond(200, detail))).toEqual({
      ok: false,
      error: "invalid run id",
    });
    expect(isRunId(RUN)).toBe(true);
  });
});

describe("safeSourceUrl", () => {
  it("allows only http(s) URLs", () => {
    expect(safeSourceUrl("http://127.0.0.1:3000/explore")).toBe("http://127.0.0.1:3000/explore");
    expect(safeSourceUrl("javascript:alert(1)")).toBeNull();
    expect(safeSourceUrl("not a url")).toBeNull();
    expect(safeSourceUrl(null)).toBeNull();
  });
});
