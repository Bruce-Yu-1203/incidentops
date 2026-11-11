// Incident history: search, filters, stats and CSV export.

import type { ApiResult } from "./api";

export const OUTCOMES = ["auto_resolved", "closed_by_person", "needs_person", "cancelled", "open"] as const;
export type HistoryOutcome = (typeof OUTCOMES)[number];

export const OUTCOME_LABEL: Record<HistoryOutcome, string> = {
  auto_resolved: "Auto-resolved",
  closed_by_person: "Closed by a person",
  needs_person: "Needs a person",
  cancelled: "Cancelled",
  open: "Open",
};

export type HistoryRow = {
  id: string;
  opened_at: string;
  title: string | null;
  service_id: string;
  severity: string;
  state: string;
  outcome: HistoryOutcome;
  disposition: string | null;
  root_cause_id: string | null;
  confidence: string | null;
  time_to_diagnosis_s: number | null;
  time_to_recovery_s: number | null;
  approved_by: string | null;
};

export type HistoryPage = {
  rows: HistoryRow[];
  total: number;
  page: number;
  page_size: number;
  stats: {
    count: number;
    auto_resolved: number;
    auto_resolved_pct: number | null;
    median_time_to_diagnosis_s: number | null;
    median_time_to_recovery_s: number | null;
  };
  services: string[];
  root_causes: string[];
};

export const RANGES = { "24h": 1, "7d": 7, "30d": 30 } as const;
export type HistoryRange = keyof typeof RANGES;

export type HistoryQuery = {
  q?: string;
  outcome?: HistoryOutcome;
  service_id?: string;
  root_cause_id?: string;
  confidence?: string;
  range: HistoryRange;
  page: number;
};

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v)?.trim() || undefined;

export function parseHistoryQuery(params: Record<string, string | string[] | undefined>): HistoryQuery {
  const outcome = one(params.outcome);
  const range = one(params.range);
  const page = Number(one(params.page) ?? "1");
  return {
    q: one(params.q)?.slice(0, 200),
    outcome: (OUTCOMES as readonly string[]).includes(outcome ?? "") ? (outcome as HistoryOutcome) : undefined,
    service_id: one(params.service_id),
    root_cause_id: one(params.root_cause_id),
    confidence: ["low", "medium", "high"].includes(one(params.confidence) ?? "") ? one(params.confidence) : undefined,
    range: range && range in RANGES ? (range as HistoryRange) : "7d",
    page: Number.isInteger(page) && page >= 1 ? page : 1,
  };
}

/** Query string for the API (``since`` computed from the range). */
export function apiParams(query: HistoryQuery, now: Date, pageSize = 25): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of ["q", "outcome", "service_id", "root_cause_id", "confidence"] as const) {
    const value = query[key];
    if (value) params.set(key, value);
  }
  params.set("since", new Date(now.getTime() - RANGES[query.range] * 86_400_000).toISOString());
  params.set("page", String(query.page));
  params.set("page_size", String(pageSize));
  return params;
}

/** Query string for links within the History page (keeps filters, changes one field). */
export function pageHref(query: HistoryQuery, change: Partial<HistoryQuery>, path = "/history"): string {
  const merged = { ...query, ...change };
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(merged)) {
    if (value === undefined || value === "" || (key === "page" && value === 1) || (key === "range" && value === "7d")) continue;
    params.set(key, String(value));
  }
  const text = params.toString();
  return text ? `${path}?${text}` : path;
}

export function isHistoryPage(value: unknown): value is HistoryPage {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return Array.isArray(v.rows) && typeof v.total === "number" && typeof v.stats === "object" && v.stats !== null;
}

export async function fetchHistory(
  baseUrl: string,
  params: URLSearchParams,
  fetchImpl: typeof fetch = fetch,
): Promise<ApiResult<HistoryPage>> {
  try {
    const response = await fetchImpl(`${baseUrl}/api/v1/history?${params}`, { cache: "no-store" });
    if (!response.ok) return { ok: false, error: `HTTP ${response.status}` };
    const body: unknown = await response.json();
    return isHistoryPage(body) ? { ok: true, data: body } : { ok: false, error: "unexpected response shape" };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "request failed" };
  }
}

/** "41 s", "3 min 20 s", "1 h 5 min"; em dash when missing. */
export function formatSeconds(value: number | null): string {
  if (value === null) return "—";
  const s = Math.round(value);
  if (s < 60) return `${s} s`;
  if (s < 3600) return s % 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s / 60} min`;
  const m = Math.round(s / 60);
  return m % 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m / 60} h`;
}

const csvCell = (value: unknown) => {
  const text = value === null || value === undefined ? "" : String(value);
  // Leading formula characters are neutralised so spreadsheets do not evaluate them.
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return /[",\n\r]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
};

export const CSV_COLUMNS = [
  "id",
  "opened_at",
  "title",
  "service_id",
  "severity",
  "state",
  "outcome",
  "disposition",
  "root_cause_id",
  "confidence",
  "time_to_diagnosis_s",
  "time_to_recovery_s",
  "approved_by",
] as const;

export function toCsv(rows: readonly HistoryRow[]): string {
  const lines = [CSV_COLUMNS.join(",")];
  for (const row of rows) lines.push(CSV_COLUMNS.map((c) => csvCell(row[c])).join(","));
  return `${lines.join("\n")}\n`;
}
