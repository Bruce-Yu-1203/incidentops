// Requests for handed-over incidents (ADR-021): assign, close, re-check.
// The API re-validates role, state and version; the UI only relays intent.

import type { ApiResult } from "./api";
import { DISPOSITIONS, type Disposition, isIncidentId } from "./incidents";

export type RecheckCheck = { check_id: string; passed: boolean | null; observed: string };
export type RecheckResult = {
  outcome: "PASSED" | "VERIFICATION_FAILED" | "UNKNOWN";
  checks: RecheckCheck[];
  checked_at: string;
  based_on: string;
  alert_cleared_at: string | null;
};

type Json = Record<string, unknown>;
const isObject = (v: unknown): v is Json => typeof v === "object" && v !== null;

async function post(
  url: string,
  body: Json | null,
  fetchImpl: typeof fetch,
): Promise<ApiResult<Json>> {
  try {
    const response = await fetchImpl(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: body === null ? undefined : JSON.stringify(body),
    });
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = isObject(payload) ? payload.detail : undefined;
      const outcome = isObject(detail) ? detail.outcome : detail;
      return { ok: false, error: typeof outcome === "string" ? outcome : `HTTP ${response.status}` };
    }
    return isObject(payload) ? { ok: true, data: payload } : { ok: false, error: "unexpected response shape" };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "request failed" };
  }
}

export function isDisposition(value: unknown): value is Disposition {
  return typeof value === "string" && (DISPOSITIONS as readonly string[]).includes(value);
}

export async function assignIncident(
  baseUrl: string,
  incidentId: string,
  expectedVersion: number,
  fetchImpl: typeof fetch = fetch,
): Promise<ApiResult<Json>> {
  if (!isIncidentId(incidentId)) return { ok: false, error: "invalid incident id" };
  return post(`${baseUrl}/api/v1/incidents/${incidentId}/assign`, { expected_version: expectedVersion }, fetchImpl);
}

export async function closeIncident(
  baseUrl: string,
  incidentId: string,
  expectedVersion: number,
  disposition: string,
  note: string | undefined,
  fetchImpl: typeof fetch = fetch,
): Promise<ApiResult<Json>> {
  if (!isIncidentId(incidentId)) return { ok: false, error: "invalid incident id" };
  if (!isDisposition(disposition)) return { ok: false, error: "choose a reason" };
  const body: Json = { expected_version: expectedVersion, disposition };
  if (note) body.note = note.slice(0, 2000);
  return post(`${baseUrl}/api/v1/incidents/${incidentId}/close`, body, fetchImpl);
}

export async function recheckIncident(
  baseUrl: string,
  incidentId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ApiResult<RecheckResult>> {
  if (!isIncidentId(incidentId)) return { ok: false, error: "invalid incident id" };
  const result = await post(`${baseUrl}/api/v1/incidents/${incidentId}/recheck`, null, fetchImpl);
  if (!result.ok) return result;
  const data = result.data;
  return typeof data.outcome === "string" && Array.isArray(data.checks)
    ? { ok: true, data: data as unknown as RecheckResult }
    : { ok: false, error: "unexpected response shape" };
}

/** Compact, URL-safe summary of a re-check for the redirect after the action. */
export function encodeRecheck(result: RecheckResult): string {
  const checks = result.checks.map((c) => `${c.passed === true ? "1" : c.passed === false ? "0" : "?"}${c.observed}`.slice(0, 160));
  return JSON.stringify({ o: result.outcome, c: checks.slice(0, 6), t: result.checked_at });
}

export function decodeRecheck(
  value: string | undefined,
): { outcome: string; checks: { passed: boolean | null; observed: string }[]; at: string } | null {
  if (!value) return null;
  try {
    const raw: unknown = JSON.parse(value);
    if (!isObject(raw) || typeof raw.o !== "string" || !Array.isArray(raw.c)) return null;
    return {
      outcome: raw.o,
      at: typeof raw.t === "string" ? raw.t : "",
      checks: raw.c
        .filter((c): c is string => typeof c === "string")
        .map((c) => ({ passed: c[0] === "1" ? true : c[0] === "0" ? false : null, observed: c.slice(1) })),
    };
  } catch {
    return null;
  }
}
