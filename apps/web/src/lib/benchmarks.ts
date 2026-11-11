// Published benchmark runs (docs/benchmark-results) as served by the API.

import type { ApiResult } from "./api";

export type TrialView = {
  trial_id: string;
  case_id: string;
  status: string;
  predicted_root_causes: string[];
  diagnosis_correct: boolean | null;
  action_correct: boolean | null;
  verified_resolution: boolean | null;
  safe_abstention: boolean | null;
  unsafe_mutations: number;
  time_to_detect_s: number | null;
  time_to_diagnosis_s: number | null;
  time_to_resolution_s: number | null;
  reasons: string[];
};

export type CaseView = {
  case_id: string;
  trials: TrialView[];
  passed: number;
  median_time_to_detect_s: number | null;
};

export type RunView = {
  run_id: string;
  folder: string;
  variant: string;
  model_id: string;
  started_at: string;
  trials_per_case: number;
  attempted: number;
  passed: number;
  unsafe_mutations: number;
  median_time_to_detect_s: number | null;
  cases: CaseView[];
};

export const CASE_NAMES: Record<string, string> = {
  "B-F01": "PostgreSQL lock contention",
  "B-F02": "Redis unavailable",
  "B-F03": "Kafka consumer paused",
  "B-F04": "API memory leak",
  "B-F05": "Bad deployment",
  "B-F06": "CPU saturation",
  "B-N01": "Transient noise (should do nothing)",
};

export function runSummary(run: RunView) {
  const fullyPassing = run.cases.filter((c) => c.trials.length > 0 && c.passed === c.trials.length);
  return {
    passed: run.passed,
    attempted: run.attempted,
    unsafe: run.unsafe_mutations,
    medianTtd: run.median_time_to_detect_s,
    scenariosAllPass: fullyPassing.length,
    scenarios: run.cases.length,
  };
}

export async function fetchBenchmarks(
  baseUrl: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ApiResult<RunView[]>> {
  try {
    const response = await fetchImpl(`${baseUrl}/api/v1/benchmarks`, { cache: "no-store" });
    if (!response.ok) return { ok: false, error: `HTTP ${response.status}` };
    const body: unknown = await response.json();
    return Array.isArray(body) ? { ok: true, data: body as RunView[] } : { ok: false, error: "unexpected response shape" };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "request failed" };
  }
}
