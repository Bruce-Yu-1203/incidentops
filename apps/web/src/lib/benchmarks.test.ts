import { describe, expect, it } from "vitest";

import { runSummary, type RunView, type TrialView } from "./benchmarks";

const trial = (status: string): TrialView => ({
  trial_id: "t", case_id: "c", status, predicted_root_causes: [], diagnosis_correct: null,
  action_correct: null, verified_resolution: null, safe_abstention: null, unsafe_mutations: 0,
  time_to_detect_s: 40, time_to_diagnosis_s: null, time_to_resolution_s: null, reasons: [],
});

describe("runSummary", () => {
  it("counts scenarios whose trials all passed", () => {
    const run = {
      passed: 6, attempted: 7, unsafe_mutations: 0, median_time_to_detect_s: 49,
      cases: [
        { case_id: "a", trials: [trial("PASS"), trial("PASS")], passed: 2, median_time_to_detect_s: 1 },
        { case_id: "b", trials: [trial("PASS"), trial("FAIL")], passed: 1, median_time_to_detect_s: 1 },
      ],
    } as unknown as RunView;
    expect(runSummary(run)).toMatchObject({ scenariosAllPass: 1, scenarios: 2, unsafe: 0 });
  });
});
