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

export type TimelineEntry = { at: string; kind: string; summary: string; reference_id: string };

export type IncidentDetail = IncidentSummary & {
  alert_events: AlertEventView[];
  timeline: TimelineEntry[];
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

export function isIncidentDetail(value: unknown): value is IncidentDetail {
  return (
    isIncidentSummary(value) &&
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
