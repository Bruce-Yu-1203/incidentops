import { describe, expect, it } from "vitest";

import { CASE_NAMES, runSummary, unmeasuredCases, type RunView, type TrialView } from "./benchmarks";

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


describe("case names", () => {
  it("names every benchmark scenario, including B-F07 to B-F13", () => {
    const ids = [
      "B-F01", "B-F02", "B-F03", "B-F04", "B-F05", "B-F06", "B-F07",
      "B-F08", "B-F09", "B-F10", "B-F11", "B-F12", "B-F13", "B-N01",
    ];
    expect(Object.keys(CASE_NAMES).sort()).toEqual(ids);
  });
});


describe("unmeasuredCases", () => {
  it("lists catalog scenarios the published run did not measure", () => {
    const run = {
      cases: [
        { case_id: "B-F01", trials: [{}], passed: 1 },
        { case_id: "B-F07", trials: [], passed: 0 },
      ],
    } as unknown as RunView;
    const ids = unmeasuredCases(run).map((c) => c.id);
    expect(ids).not.toContain("B-F01");
    expect(ids).toContain("B-F07");
    expect(ids).toContain("B-F13");
    expect(unmeasuredCases(null)).toHaveLength(Object.keys(CASE_NAMES).length);
  });
});
