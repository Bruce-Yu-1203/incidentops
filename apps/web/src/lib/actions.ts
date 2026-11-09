// Action read model and decision requests.

import type { ApiResult } from "./api";

export type ActionDetail = {
  id: string;
  incident_id: string;
  state: string;
  version: number;
  reason_code: string | null;
  action_hash: string;
  policy_version: string;
  expires_at: string;
  envelope: Record<string, unknown>;
  approval: {
    id: string;
    state: string;
    version: number;
    expires_at: string;
    requested_by: string;
  } | null;
  decisions: {
    approver_id: string;
    decision: string;
    action_hash: string;
    reason: string | null;
    decided_at: string;
  }[];
  operations: {
    provider: string;
    provider_operation_id: string | null;
    status: string;
    receipt: Record<string, unknown> | null;
    updated_at: string;
  }[];
  verifications: {
    id: string;
    outcome: string;
    checks: { check_id: string; passed: boolean | null; observed: string }[];
    window_start: string;
    window_end: string;
  }[];
};

type Json = Record<string, unknown>;
const isObject = (v: unknown): v is Json => typeof v === "object" && v !== null;
const isString = (v: unknown): v is string => typeof v === "string";

export function isActionDetail(value: unknown): value is ActionDetail {
  return (
    isObject(value) &&
    ["id", "incident_id", "state", "action_hash", "policy_version", "expires_at"].every((k) =>
      isString(value[k]),
    ) &&
    typeof value.version === "number" &&
    /^sha256:[0-9a-f]{64}$/.test(value.action_hash as string) &&
    isObject(value.envelope) &&
    (value.approval === null || isObject(value.approval)) &&
    ["decisions", "operations", "verifications"].every((k) => Array.isArray(value[k]))
  );
}

export function isActionId(value: string): boolean {
  return /^act_[0-9a-f]{32}$/.test(value);
}

export async function fetchAction(
  baseUrl: string,
  actionId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ApiResult<ActionDetail>> {
  if (!isActionId(actionId)) return { ok: false, error: "invalid action id" };
  try {
    const response = await fetchImpl(`${baseUrl}/api/v1/actions/${actionId}`, { cache: "no-store" });
    if (!response.ok) return { ok: false, error: `HTTP ${response.status}` };
    const body: unknown = await response.json();
    return isActionDetail(body) ? { ok: true, data: body } : { ok: false, error: "unexpected response shape" };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "request failed" };
  }
}

export type DecisionKind = "approve" | "reject" | "cancel";

export type DecisionInput = {
  actionId: string;
  kind: DecisionKind;
  expectedVersion: number;
  actionHash?: string;
  reason?: string;
};

/** Build and send a decision; the API re-validates every field. */
export async function sendDecision(
  baseUrl: string,
  input: DecisionInput,
  fetchImpl: typeof fetch = fetch,
): Promise<ApiResult<{ outcome: string }>> {
  if (!isActionId(input.actionId)) return { ok: false, error: "invalid action id" };
  if (!Number.isInteger(input.expectedVersion) || input.expectedVersion < 1) {
    return { ok: false, error: "invalid version" };
  }
  const body: Json = { expected_version: input.expectedVersion };
  if (input.reason) body.reason = input.reason.slice(0, 2000);
  if (input.kind === "approve") {
    if (!input.actionHash || !/^sha256:[0-9a-f]{64}$/.test(input.actionHash)) {
      return { ok: false, error: "invalid action hash" };
    }
    body.action_hash = input.actionHash;
  }
  try {
    const response = await fetchImpl(`${baseUrl}/api/v1/actions/${input.actionId}/${input.kind}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload: unknown = await response.json();
    if (!response.ok) {
      const detail = isObject(payload) && isObject(payload.detail) ? payload.detail.outcome : undefined;
      return { ok: false, error: isString(detail) ? detail : `HTTP ${response.status}` };
    }
    return isObject(payload) && isString(payload.outcome)
      ? { ok: true, data: { outcome: payload.outcome } }
      : { ok: false, error: "unexpected response shape" };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "request failed" };
  }
}
