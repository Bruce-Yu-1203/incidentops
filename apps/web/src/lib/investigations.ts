// Investigation run read model: steps, evidence and source links.

import type { ApiResult } from "./api";
import { type DiagnosisReport, isDiagnosisReport, isRunSummary, type RunSummary } from "./incidents";

export type StepView = {
  id: string;
  kind: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  metadata: Record<string, unknown>;
};

export type EvidenceView = {
  id: string;
  source: string;
  template_id: string;
  parameters: Record<string, unknown>;
  window_start: string;
  window_end: string;
  observed_at: string;
  digest: string;
  summary: string;
  truncated: boolean;
  source_url: string | null;
};

export type RunDetail = RunSummary & {
  steps: StepView[];
  evidence: EvidenceView[];
  diagnosis: DiagnosisReport | null;
};

type Json = Record<string, unknown>;
const isObject = (v: unknown): v is Json => typeof v === "object" && v !== null;
const isString = (v: unknown): v is string => typeof v === "string";

function isStep(value: unknown): value is StepView {
  return (
    isObject(value) &&
    ["id", "kind", "status", "started_at"].every((k) => isString(value[k])) &&
    isObject(value.metadata)
  );
}

/** Only http(s) links to the configured Grafana are rendered as anchors. */
export function safeSourceUrl(value: unknown): string | null {
  if (!isString(value)) return null;
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function isEvidence(value: unknown): value is EvidenceView {
  return (
    isObject(value) &&
    ["id", "source", "template_id", "summary", "digest", "observed_at"].every((k) =>
      isString(value[k]),
    ) &&
    typeof value.truncated === "boolean" &&
    (value.source_url === null || isString(value.source_url))
  );
}

export function isRunDetail(value: unknown): value is RunDetail {
  return (
    isRunSummary(value) &&
    Array.isArray((value as Json).steps) &&
    ((value as Json).steps as unknown[]).every(isStep) &&
    Array.isArray((value as Json).evidence) &&
    ((value as Json).evidence as unknown[]).every(isEvidence) &&
    ((value as Json).diagnosis === null || isDiagnosisReport((value as Json).diagnosis))
  );
}

export function isRunId(value: string): boolean {
  return /^run_[0-9a-f]{32}$/.test(value);
}

export async function fetchRun(
  baseUrl: string,
  runId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ApiResult<RunDetail>> {
  if (!isRunId(runId)) return { ok: false, error: "invalid run id" };
  try {
    const response = await fetchImpl(`${baseUrl}/api/v1/investigations/${runId}`, {
      cache: "no-store",
    });
    if (!response.ok) return { ok: false, error: `HTTP ${response.status}` };
    const body: unknown = await response.json();
    return isRunDetail(body) ? { ok: true, data: body } : { ok: false, error: "unexpected response shape" };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "request failed" };
  }
}
