import { describe, expect, it } from "vitest";

import { diagnosisRun, evidenceHref, shortId } from "./evidence";
import type { RunSummary } from "./incidents";

const run = (id: string, state: string, started_at: string): RunSummary => ({
  id,
  incident_id: "inc_1",
  state,
  model_id: null,
  error_code: null,
  step_count: 0,
  tool_call_count: 0,
  input_tokens: 0,
  output_tokens: 0,
  started_at,
  finished_at: null,
});

describe("diagnosisRun", () => {
  it("prefers the latest completed run", () => {
    const runs = [
      run("run_a", "COMPLETED", "2026-11-09T12:00:00Z"),
      run("run_b", "COMPLETED", "2026-11-09T12:05:00Z"),
      run("run_c", "FAILED", "2026-11-09T12:10:00Z"),
    ];
    expect(diagnosisRun(runs)?.id).toBe("run_b");
  });

  it("falls back to the latest run, or null", () => {
    expect(diagnosisRun([run("run_a", "RUNNING", "2026-11-09T12:00:00Z")])?.id).toBe("run_a");
    expect(diagnosisRun([])).toBeNull();
  });
});

describe("evidenceHref", () => {
  it("links to the evidence anchor on the run page", () => {
    expect(evidenceHref("run_x", "ev_abc123")).toBe("/investigations/run_x#ev_abc123");
  });

  it("refuses malformed ids and missing runs", () => {
    expect(evidenceHref("run_x", "ev_<script>")).toBeNull();
    expect(evidenceHref(null, "ev_abc")).toBeNull();
  });
});

describe("shortId", () => {
  it("keeps the prefix and the tail", () => {
    expect(shortId(`ev_${"a".repeat(26)}123456`)).toBe("ev_…123456");
    expect(shortId("ev_abc")).toBe("ev_abc");
  });
});
