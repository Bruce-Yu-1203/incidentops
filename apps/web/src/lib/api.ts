// Typed client helpers for the IncidentOps control-plane API.
// The UI never makes authorization decisions; it only renders server state.

export type HealthStatus = {
  status: "ok";
  version: string;
  profile: string;
};

export type ApiResult<T> = { ok: true; data: T } | { ok: false; error: string };

/** Resolve the API base URL from the environment, defaulting to local dev. */
export function apiBaseUrl(env: Record<string, string | undefined> = process.env): string {
  const raw = env.INCIDENTOPS_API_URL ?? "http://127.0.0.1:8000";
  const url = new URL(raw);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error(`unsupported API protocol: ${url.protocol}`);
  }
  return url.origin;
}

function isHealthStatus(value: unknown): value is HealthStatus {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    record.status === "ok" &&
    typeof record.version === "string" &&
    typeof record.profile === "string"
  );
}

/** Fetch the control-plane liveness endpoint and validate its shape. */
export async function fetchHealth(
  baseUrl: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ApiResult<HealthStatus>> {
  try {
    const response = await fetchImpl(`${baseUrl}/healthz`, { cache: "no-store" });
    if (!response.ok) {
      return { ok: false, error: `HTTP ${response.status}` };
    }
    const body: unknown = await response.json();
    return isHealthStatus(body)
      ? { ok: true, data: body }
      : { ok: false, error: "unexpected response shape" };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "request failed" };
  }
}
