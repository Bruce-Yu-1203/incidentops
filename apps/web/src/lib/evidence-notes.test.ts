import { describe, expect, it } from "vitest";

import { byRole, evidenceNotes, templateSentence } from "./evidence-notes";
import type { EvidenceView } from "./investigations";

const ev = (id: string, summary: string, source = "prometheus"): EvidenceView => ({
  id,
  source,
  template_id: "kafka_group_lag",
  parameters: {},
  window_start: "",
  window_end: "",
  observed_at: "",
  digest: "",
  summary,
  truncated: false,
  source_url: null,
});

describe("evidenceNotes", () => {
  it("groups by role and prefers the agent's sentence", () => {
    const notes = evidenceNotes(
      {
        supporting_evidence_ids: ["ev_a", "ev_missing"],
        contradicting_evidence_ids: ["ev_b"],
        evidence_notes: { ev_a: "Lag grew from 0 to 41k." },
      },
      [ev("ev_a", "x"), ev("ev_b", "Deploy landed 2 min before. More text."), ev("ev_c", "ok")],
    );
    expect(byRole(notes, "supports").map((n) => n.sentence)).toEqual([
      "Lag grew from 0 to 41k.",
      "Record ev_missing is cited but was not found in this run.",
    ]);
    expect(byRole(notes, "against")[0].sentence).toBe(
      "Metric (consumer lag): Deploy landed 2 min before.",
    );
    expect(byRole(notes, "set_aside").map((n) => n.id)).toEqual(["ev_c"]);
    expect(notes[0].fromAgent).toBe(true);
  });

  it("puts everything in set aside without a diagnosis", () => {
    expect(evidenceNotes(null, [ev("ev_a", "s")])[0].role).toBe("set_aside");
  });
});

describe("templateSentence", () => {
  it("truncates long summaries", () => {
    expect(templateSentence(ev("e", "y".repeat(400), "loki"), "e").length).toBeLessThan(200);
  });
});

describe("templateSentence for new evidence", () => {
  it("names new templates and controller sources in plain words", () => {
    const base = {
      id: "evd_1",
      run_id: "run_1",
      window_start: "2026-11-12T10:00:00Z",
      window_end: "2026-11-12T10:05:00Z",
      observed_at: "2026-11-12T10:05:00Z",
      parameters: {},
      snapshot: "",
      truncated: false,
    };
    expect(
      templateSentence(
        { ...base, source: "deployment_controller", template_id: "recent_config_changes",
          summary: "lab-api config cfg-2 (last known good cfg-1)" } as never,
        "evd_1",
      ),
    ).toBe("Lab controller (recent config changes): lab-api config cfg-2 (last known good cfg-1)");
    expect(
      templateSentence(
        { ...base, source: "prometheus", template_id: "api_memory_growth_rate",
          summary: "Resident memory growth rate rose to 2.5 MB/s." } as never,
        "evd_1",
      ),
    ).toBe("Metric (API memory growth rate): Resident memory growth rate rose to 2.5 MB/s.");
  });
});
