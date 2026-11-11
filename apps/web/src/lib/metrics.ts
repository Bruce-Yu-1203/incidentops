// Live metric panels: API client and SVG geometry for the native charts.

import type { ApiResult } from "./api";

export type SeriesView = { name: string; points: [number, number][] };

export type PanelView = {
  panel_id: string;
  title: string;
  unit: string;
  threshold: number | null;
  status: "ok" | "over" | "unavailable";
  latest: number | null;
  series: SeriesView[];
};

export type IncidentWindow = {
  incident_id: string;
  title: string | null;
  service_id: string;
  start: string;
  end: string | null;
};

export type MetricsView = {
  start: string;
  end: string;
  step_seconds: number;
  grafana_dashboard_url: string;
  panels: PanelView[];
  incidents: IncidentWindow[];
};

export const METRIC_RANGES = [15, 30, 60, 360] as const;
export type MetricRange = (typeof METRIC_RANGES)[number];

export function parseRange(value: string | string[] | undefined): MetricRange {
  const n = Number(Array.isArray(value) ? value[0] : value);
  return (METRIC_RANGES as readonly number[]).includes(n) ? (n as MetricRange) : 30;
}

export const rangeLabel = (m: MetricRange) => (m < 60 ? `${m}m` : `${m / 60}h`);

function isMetricsView(value: unknown): value is MetricsView {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return Array.isArray(v.panels) && Array.isArray(v.incidents) && typeof v.start === "string";
}

export async function fetchMetrics(
  baseUrl: string,
  minutes: MetricRange,
  fetchImpl: typeof fetch = fetch,
): Promise<ApiResult<MetricsView>> {
  try {
    const response = await fetchImpl(`${baseUrl}/api/v1/metrics?minutes=${minutes}`, { cache: "no-store" });
    if (!response.ok) return { ok: false, error: `HTTP ${response.status}` };
    const body: unknown = await response.json();
    return isMetricsView(body) ? { ok: true, data: body } : { ok: false, error: "unexpected response shape" };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "request failed" };
  }
}

export type ChartGeometry = {
  paths: { name: string; d: string }[];
  thresholdY: number | null;
  max: number;
  bands: { x: number; width: number; id: string }[];
};

/** Polyline paths in a ``width``×``height`` viewBox; the y range includes the threshold. */
export function chartGeometry(
  panel: Pick<PanelView, "series" | "threshold">,
  startMs: number,
  endMs: number,
  windows: readonly IncidentWindow[] = [],
  width = 600,
  height = 160,
): ChartGeometry {
  const values = panel.series.flatMap((s) => s.points.map(([, v]) => v)).filter(Number.isFinite);
  const max = Math.max(...values, panel.threshold ?? 0, 1e-9) * 1.12;
  const span = Math.max(endMs - startMs, 1);
  const x = (ms: number) => ((ms - startMs) / span) * width;
  const y = (v: number) => height - (v / max) * height;
  const paths = panel.series.map((s) => ({
    name: s.name,
    d: s.points
      .filter(([, v]) => Number.isFinite(v))
      .map(([ts, v], i) => `${i === 0 ? "M" : "L"}${x(ts * 1000).toFixed(1)},${y(v).toFixed(1)}`)
      .join(" "),
  }));
  const bands = windows
    .map((w) => {
      const from = Math.max(new Date(w.start).getTime(), startMs);
      const to = Math.min(w.end ? new Date(w.end).getTime() : endMs, endMs);
      return { x: x(from), width: Math.max(x(to) - x(from), 2), id: w.incident_id };
    })
    .filter((b) => b.x <= width && b.width > 0);
  return {
    paths,
    thresholdY: panel.threshold === null ? null : y(panel.threshold),
    max,
    bands,
  };
}

export function formatValue(value: number | null, unit: string): string {
  if (value === null) return "no data";
  switch (unit) {
    case "seconds":
      return value < 1 ? `${Math.round(value * 1000)} ms` : `${value.toFixed(2)} s`;
    case "ratio":
      return `${(value * 100).toFixed(1)} %`;
    case "bytes":
      return value >= 1e9 ? `${(value / 1e9).toFixed(1)} GB` : `${(value / 1e6).toFixed(1)} MB`;
    case "messages":
      return value >= 1000 ? `${(value / 1000).toFixed(1)}k` : `${Math.round(value)}`;
    default:
      return value >= 100 ? `${Math.round(value)}` : value.toFixed(1);
  }
}

/** Which panel best shows an incident's service, for the Story chart. */
export function panelForService(serviceId: string, title?: string | null): string | null {
  if (serviceId.includes("consumer")) return "consumer_lag";
  if (serviceId === "lab-api") return title?.includes("error") ? "api_5xx" : "api_p95";
  if (serviceId === "lab-postgres") return "pg_connections";
  if (serviceId === "lab-redis") return "redis_memory";
  return null;
}
