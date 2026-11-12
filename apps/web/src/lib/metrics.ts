// Live metric panels: API client and SVG geometry for the native charts.

import type { ApiResult } from "./api";

export type SeriesView = { name: string; points: [number, number][] };

export type PanelView = {
  panel_id: string;
  title: string;
  unit: string;
  threshold: number | null;
  threshold_direction?: "above" | "below";
  status: "ok" | "over" | "unavailable" | "no_data" | "partial";
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

export function isMetricsView(value: unknown): value is MetricsView {
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
  min: number;
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
  expectedStepSeconds?: number,
): ChartGeometry {
  const inRange = (ts: number) => ts * 1000 >= startMs && ts * 1000 <= endMs;
  const values = panel.series.flatMap((s) => s.points.filter(([ts]) => inRange(ts)).map(([, v]) => v)).filter(Number.isFinite);
  const min = Math.min(0, ...values, panel.threshold ?? 0);
  const ceiling = Math.max(...values, panel.threshold ?? 0);
  const max = (ceiling > 0 ? ceiling : 1) * 1.12;
  const span = Math.max(endMs - startMs, 1);
  const x = (ms: number) => ((ms - startMs) / span) * width;
  const y = (v: number) => height - ((v - min) / (max - min)) * height;
  const paths = panel.series.map((s) => {
    let connected = false;
    let previous = 0;
    const commands: string[] = [];
    for (const [ts, value] of s.points) {
      if (!inRange(ts) || !Number.isFinite(value)) { connected = false; continue; }
      if (expectedStepSeconds && ts - previous > expectedStepSeconds * 3) connected = false;
      commands.push(`${connected ? "L" : "M"}${x(ts * 1000).toFixed(1)},${y(value).toFixed(1)}`);
      connected = true;
      previous = ts;
    }
    return { name: s.name, d: commands.join(" ") };
  });
  const rawBands = windows.flatMap((w) => {
    const from = Math.max(new Date(w.start).getTime(), startMs);
    const to = Math.min(w.end ? new Date(w.end).getTime() : endMs, endMs);
    if (to <= from) return [];
    return [{ x: x(from), width: x(to) - x(from), id: w.incident_id }];
  });
  const bands: ChartGeometry["bands"] = [];
  for (const band of rawBands.sort((a, b) => a.x - b.x)) {
    const previous = bands.at(-1);
    if (previous && band.x <= previous.x + previous.width) {
      previous.width = Math.max(previous.x + previous.width, band.x + band.width) - previous.x;
    } else bands.push({ ...band });
  }
  return {
    paths,
    thresholdY: panel.threshold === null ? null : y(panel.threshold),
    max,
    min,
    bands,
  };
}

export function formatValue(value: number | null, unit: string): string {
  if (value === null) return "no data";
  switch (unit) {
    case "bool":
      return value >= 0.5 ? "Available" : "Unavailable";
    case "cores":
      return `${value.toFixed(2)} cores`;
    case "bytes/s":
      return `${Number((value / 1e6).toFixed(2)).toFixed(2)} MB/s`;
    case "messages/s":
      return `${value.toFixed(1)} msg/s`;
    case "requests/s":
      return `${value.toFixed(1)} req/s`;
    case "brokers":
      return `${Math.round(value)} broker${Math.round(value) === 1 ? "" : "s"}`;
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
  if (serviceId.includes("consumer")) return title?.includes("processing") ? "produce_consume" : "consumer_lag";
  if (serviceId === "lab-kafka") return "kafka_brokers";
  if (serviceId === "lab-api") {
    if (title?.includes("memory")) return "api_memory_growth";
    if (title?.includes("cpu")) return "api_cpu";
    if (title?.includes("request_rate")) return "api_request_rate";
    return title?.includes("error") ? "api_5xx" : "api_p95";
  }
  if (serviceId === "lab-postgres") {
    if (title?.includes("down")) return "pg_up";
    if (title?.includes("connections")) return "pg_pool_usage";
    return "pg_locks";
  }
  if (serviceId === "lab-redis") return "redis_up";
  return null;
}


export type MetricMarker = { at: string; label: string };
export type IncidentMetricsView = MetricsView & {
  markers: MetricMarker[];
  mode: "live" | "historical";
};

export async function fetchIncidentMetrics(
  baseUrl: string, incidentId: string, fetchImpl: typeof fetch = fetch,
): Promise<ApiResult<IncidentMetricsView>> {
  try {
    const response = await fetchImpl(`${baseUrl}/api/v1/incidents/${incidentId}/metrics`, { cache: "no-store" });
    if (!response.ok) return { ok: false, error: `HTTP ${response.status}` };
    const body: unknown = await response.json();
    return isMetricsView(body) && "markers" in body && Array.isArray(body.markers) && "mode" in body
      ? { ok: true, data: body as IncidentMetricsView }
      : { ok: false, error: "unexpected response shape" };
  } catch {
    return { ok: false, error: "Could not load incident metrics" };
  }
}


export const METRIC_GROUPS = [
  { title: "API", description: "Request health and process resources", service: "lab-api",
    ids: ["api_p95", "api_5xx", "api_request_rate", "api_cpu", "api_memory", "api_memory_growth"] },
  { title: "Messaging", description: "Consumer backlog, throughput and broker visibility", service: "persist-demo-consumer",
    ids: ["consumer_lag", "produce_consume", "kafka_brokers"] },
  { title: "PostgreSQL", description: "Availability, connection usage and lock contention", service: "lab-postgres",
    ids: ["pg_up", "pg_pool_usage", "pg_connections", "pg_locks"] },
  { title: "Redis", description: "Dependency availability and memory", service: "lab-redis",
    ids: ["redis_up", "redis_memory"] },
] as const;

export function panelWindows(panelId: string, windows: readonly IncidentWindow[]): IncidentWindow[] {
  const group = METRIC_GROUPS.find((g) => (g.ids as readonly string[]).includes(panelId));
  return windows.filter((w) => w.service_id === group?.service);
}

export function panelHealth(panel: PanelView, endMs: number, stepSeconds: number): string {
  if (panel.status === "unavailable") return "Source unavailable";
  if (panel.status === "no_data" || !panel.series.some((s) => s.points.length)) return "No data";
  if (panel.status === "partial") return "Partial data";
  const timestamps = panel.series.flatMap((s) => s.points.map(([ts]) => ts));
  if (endMs - Math.max(...timestamps) * 1000 > Math.max(30, stepSeconds * 3) * 1000) return "Stale data";
  return panel.status === "over" ? "Threshold crossed" : panel.threshold === null ? "Observed" : "Within range";
}
