import { describe, expect, it } from "vitest";

import type { EvidenceView, StepView } from "./investigations";
import { evidenceGraph, stepRow } from "./trajectory";

const step = (kind: string, status: string, metadata: Record<string, unknown>): StepView => ({
  id: `stp_${kind}`,
  kind,
  status,
  started_at: "2026-11-09T12:00:00Z",
  finished_at: "2026-11-09T12:00:02Z",
  metadata,
});

const evidence = (id: string): EvidenceView => ({
  id,
  source: "prometheus",
  template_id: "consumer_lag",
  parameters: {},
  window_start: "2026-11-09T11:50:00Z",
  window_end: "2026-11-09T12:00:00Z",
  observed_at: "2026-11-09T12:00:00Z",
  digest: "sha256:0",
  summary: "lag high",
  truncated: false,
  source_url: null,
});

describe("stepRow", () => {
  it("highlights a denied tool call with its redacted arguments", () => {
    const row = stepRow(
      step("tool_call", "DENIED", {
        tool: "query_logs",
        arguments: { service: "lab-db" },
        error_code: "unregistered_target",
      }),
    );
    expect(row).toMatchObject({
      title: "query_logs",
      tone: "danger",
      blocked: true,
      args: '{"service":"lab-db"}',
      duration: "2 s",
    });
    expect(row.detail).toContain("unregistered_target");
  });

  it("describes successful tools, model calls and diagnoses", () => {
    expect(
      stepRow(step("tool_call", "SUCCESS", { tool: "query_metrics", evidence_id: "ev_1" })),
    ).toMatchObject({ tone: "success", blocked: false, detail: "SUCCESS · → ev_1" });
    expect(stepRow(step("model_call", "SUCCESS", { tool_calls: ["a", "b"] })).detail).toBe(
      "requested a, b",
    );
    expect(
      stepRow(step("diagnosis", "SUCCESS", { root_cause_id: "consumer_paused" })),
    ).toMatchObject({ title: "diagnosis", detail: "consumer_paused" });
  });

  it("leaves duration empty for an unfinished step", () => {
    expect(stepRow({ ...step("run", "RUNNING", {}), finished_at: null }).duration).toBeNull();
  });
});

describe("evidenceGraph", () => {
  it("splits evidence by how the diagnosis cites it", () => {
    const graph = evidenceGraph(
      { supporting_evidence_ids: ["ev_a", "ev_missing"], contradicting_evidence_ids: ["ev_b"] },
      [evidence("ev_a"), evidence("ev_b"), evidence("ev_c")],
    );
    expect(graph.supporting.map((n) => [n.id, n.evidence?.id ?? null])).toEqual([
      ["ev_a", "ev_a"],
      ["ev_missing", null],
    ]);
    expect(graph.contradicting.map((n) => n.id)).toEqual(["ev_b"]);
    expect(graph.uncited.map((e) => e.id)).toEqual(["ev_c"]);
  });

  it("treats all evidence as uncited without a diagnosis", () => {
    expect(evidenceGraph(null, [evidence("ev_a")]).uncited).toHaveLength(1);
  });
});
