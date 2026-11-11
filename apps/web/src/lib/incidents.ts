// Incident read-model client with runtime shape validation.
// API responses are treated as untrusted input until validated.

import type { ApiResult } from "./api";

export const INCIDENT_STATES = [
  "RECEIVED",
  "TRIAGED",
  "INVESTIGATING",
  "DIAGNOSED",
  "DIAGNOSED_ONLY",
  "INSUFFICIENT_EVIDENCE",
  "AWAITING_APPROVAL",
  "EXECUTING",
  "VERIFYING",
  "RESOLVED",
  "ESCALATED",
  "FAILED",
  "CANCELLED",
] as const;

export type IncidentState = (typeof INCIDENT_STATES)[number];

export type IncidentSummary = {
  id: string;
  dedup_key: string;
  service_id: string;
  environment: string;
  severity: string;
  state: IncidentState;
  version: number;
  created_at: string;
  updated_at: string;
  /** Alert name of the first delivery; the incident's display title. */
  title?: string | null;
  owner_username?: string | null;
  assigned_at?: string | null;
  disposition?: Disposition | null;
  disposition_note?: string | null;
  disposed_by?: string | null;
  disposed_at?: string | null;
  /** Handed over to a person and not yet closed by one (ADR-021). */
  needs_human?: boolean;
};

export const DISPOSITIONS = [
  "FIXED_MANUALLY",
  "RECOVERED_ON_ITS_OWN",
  "FALSE_ALARM",
  "WONT_FIX",
] as const;

export type Disposition = (typeof DISPOSITIONS)[number];

export const DISPOSITION_LABEL: Record<Disposition, string> = {
  FIXED_MANUALLY: "I fixed it by hand",
  RECOVERED_ON_ITS_OWN: "It recovered on its own",
  FALSE_ALARM: "False alarm",
  WONT_FIX: "Won't fix",
};

export type AlertEventView = {
  id: string;
  status: string;
  alert_name: string;
  fingerprint: string;
  starts_at: string;
  received_at: string;
  labels: Record<string, string>;
  annotations: Record<string, string>;
};

export type TimelineEntry = {
  at: string;
  kind: string;
  summary: string;
  reference_id: string;
  actor?: string | null;
  event_type?: string | null;
  decision?: string | null;
  resource_type?: string | null;
};

export type RunSummary = {
  id: string;
  incident_id: string;
  state: string;
  model_id: string | null;
  error_code: string | null;
  step_count: number;
  tool_call_count: number;
  input_tokens: number;
  output_tokens: number;
  started_at: string | null;
  finished_at: string | null;
};

export type Diagnosis = {
  root_cause_id: string;
  affected_component: string;
  confidence_level: string;
  summary: string;
  supporting_evidence_ids: string[];
  contradicting_evidence_ids: string[];
  unresolved_questions: string[];
  /** Optional plain-language sentence per cited evidence id. */
  evidence_notes?: Record<string, string>;
};

export type ProposedAction = {
  action_type: string;
  environment: string;
  target_id: string;
  arguments: Record<string, unknown>;
  reason: string;
  evidence_ids: string[];
};

export type DiagnosisReport = {
  diagnosis: Diagnosis;
  proposed_action: ProposedAction | null;
  needs_human_review: boolean;
};

export type ActionSummary = {
  id: string;
  action_type: string;
  target_id: string;
  state: string;
  version: number;
  expires_at: string;
};

const isActionSummary = (value: unknown): value is ActionSummary =>
  isObject(value) &&
  ["id", "action_type", "target_id", "state", "expires_at"].every((k) => isString(value[k])) &&
  typeof value.version === "number";

export type IncidentDetail = IncidentSummary & {
  actions: ActionSummary[];
  escalation_reason: string | null;
  alert_events: AlertEventView[];
  timeline: TimelineEntry[];
  runs: RunSummary[];
  diagnosis: DiagnosisReport | null;
};

type Json = Record<string, unknown>;

const isObject = (value: unknown): value is Json => typeof value === "object" && value !== null;
const isString = (value: unknown): value is string => typeof value === "string";
const isStringMap = (value: unknown): value is Record<string, string> =>
  isObject(value) && Object.values(value).every(isString);

export function isIncidentSummary(value: unknown): value is IncidentSummary {
  return (
    isObject(value) &&
    ["id", "dedup_key", "service_id", "environment", "severity", "created_at", "updated_at"].every(
      (key) => isString(value[key]),
    ) &&
    typeof value.version === "number" &&
    (INCIDENT_STATES as readonly unknown[]).includes(value.state)
  );
}

function isAlertEvent(value: unknown): value is AlertEventView {
  return (
    isObject(value) &&
    ["id", "status", "alert_name", "fingerprint", "starts_at", "received_at"].every((key) =>
      isString(value[key]),
    ) &&
    isStringMap(value.labels) &&
    isStringMap(value.annotations)
  );
}

function isTimelineEntry(value: unknown): value is TimelineEntry {
  return isObject(value) && ["at", "kind", "summary", "reference_id"].every((k) => isString(value[k]));
}

const isNullableString = (value: unknown): boolean => value === null || isString(value);
const isStringArray = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every(isString);

export function isRunSummary(value: unknown): value is RunSummary {
  return (
    isObject(value) &&
    isString(value.id) &&
    isString(value.incident_id) &&
    isString(value.state) &&
    ["model_id", "error_code", "started_at", "finished_at"].every((k) => isNullableString(value[k])) &&
    ["step_count", "tool_call_count", "input_tokens", "output_tokens"].every(
      (k) => typeof value[k] === "number",
    )
  );
}

export function isDiagnosisReport(value: unknown): value is DiagnosisReport {
  if (!isObject(value) || !isObject(value.diagnosis)) return false;
  const d = value.diagnosis;
  const actionOk =
    value.proposed_action === null ||
    value.proposed_action === undefined ||
    (isObject(value.proposed_action) &&
      ["action_type", "environment", "target_id", "reason"].every((k) =>
        isString((value.proposed_action as Json)[k]),
      ) &&
      isStringArray((value.proposed_action as Json).evidence_ids));
  return (
    ["root_cause_id", "affected_component", "confidence_level", "summary"].every((k) =>
      isString(d[k]),
    ) &&
    isStringArray(d.supporting_evidence_ids) &&
    actionOk
  );
}

export function isIncidentDetail(value: unknown): value is IncidentDetail {
  return (
    isIncidentSummary(value) &&
    isNullableString((value as Json).escalation_reason) &&
    Array.isArray((value as Json).actions) &&
    ((value as Json).actions as unknown[]).every(isActionSummary) &&
    Array.isArray((value as Json).runs) &&
    ((value as Json).runs as unknown[]).every(isRunSummary) &&
    ((value as Json).diagnosis === null || isDiagnosisReport((value as Json).diagnosis)) &&
    Array.isArray((value as Json).alert_events) &&
    ((value as Json).alert_events as unknown[]).every(isAlertEvent) &&
    Array.isArray((value as Json).timeline) &&
    ((value as Json).timeline as unknown[]).every(isTimelineEntry)
  );
}

async function getJson<T>(
  url: string,
  guard: (value: unknown) => value is T,
  fetchImpl: typeof fetch,
): Promise<ApiResult<T>> {
  try {
    const response = await fetchImpl(url, { cache: "no-store" });
    if (!response.ok) return { ok: false, error: `HTTP ${response.status}` };
    const body: unknown = await response.json();
    return guard(body) ? { ok: true, data: body } : { ok: false, error: "unexpected response shape" };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "request failed" };
  }
}

export function fetchIncidents(
  baseUrl: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ApiResult<IncidentSummary[]>> {
  const guard = (value: unknown): value is IncidentSummary[] =>
    Array.isArray(value) && value.every(isIncidentSummary);
  return getJson(`${baseUrl}/api/v1/incidents`, guard, fetchImpl);
}

/** Incident IDs are validated before use so they cannot alter the request path. */
export function isIncidentId(value: string): boolean {
  return /^inc_[0-9a-f]{32}$/.test(value);
}

export function fetchIncident(
  baseUrl: string,
  incidentId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ApiResult<IncidentDetail>> {
  if (!isIncidentId(incidentId)) {
    return Promise.resolve({ ok: false, error: "invalid incident id" });
  }
  return getJson(`${baseUrl}/api/v1/incidents/${incidentId}`, isIncidentDetail, fetchImpl);
}
