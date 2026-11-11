import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { AutoRefresh } from "@/components/auto-refresh";
import { LineChart } from "@/components/line-chart";
import { apiBaseUrl } from "@/lib/api";
import { incidentTitle } from "@/lib/inbox";
import { fetchMetrics, formatValue, METRIC_RANGES, parseRange, rangeLabel } from "@/lib/metrics";
import { sessionFetch } from "@/lib/session";
import { LoadError } from "@/app/load-error";

type Search = Promise<Record<string, string | string[] | undefined>>;
const REFRESH_MS = 15_000;

async function MetricsView({ searchParams }: { searchParams: Search }) {
  await connection();
  const range = parseRange((await searchParams).range);
  const result = await fetchMetrics(apiBaseUrl(), range, await sessionFetch());
  if (!result.ok) return <LoadError what="metrics" error={result.error} next="/metrics" />;
  const view = result.data;
  const start = new Date(view.start).getTime();
  const end = new Date(view.end).getTime();
  return (
    <>
      <div className="row between">
        <span className="segmented" role="group" aria-label="Time range">
          {METRIC_RANGES.map((m) => (
            <Link key={m} href={`/metrics?range=${m}`} aria-current={m === range ? "true" : undefined}>
              {rangeLabel(m)}
            </Link>
          ))}
        </span>
        <span className="row small">
          <AutoRefresh active intervalMs={REFRESH_MS} />
          <a href={view.grafana_dashboard_url} target="_blank" rel="noreferrer">
            Open in Grafana ↗
          </a>
        </span>
      </div>
      {view.incidents.length > 0 ? (
        <p className="small muted" style={{ margin: 0 }}>
          Shaded: incident windows for{" "}
          {[...new Set(view.incidents.map((w) => incidentTitle({ title: w.title, dedup_key: w.incident_id })))].join(", ")}
        </p>
      ) : null}
      <div className="charts">
        {view.panels.map((panel) => (
          <section key={panel.panel_id} className={`chart-card ${panel.status === "over" ? "chart-over" : ""}`}>
            <div className="row between">
              <strong className="small">{panel.title}</strong>
              <span className="value">{formatValue(panel.latest, panel.unit)}</span>
            </div>
            {panel.status === "unavailable" ? (
              <p className="small muted">Prometheus did not answer.</p>
            ) : (
              <LineChart panel={panel} startMs={start} endMs={end} windows={view.incidents} />
            )}
            <span className="tiny muted">
              {panel.threshold !== null ? `Alert above ${formatValue(panel.threshold, panel.unit)}` : "No alert on this metric"}
              {panel.series.length > 1 ? ` · ${panel.series.map((s) => s.name).join(" vs ")}` : ""}
              {panel.status === "over" ? " · over threshold now" : ""}
            </span>
          </section>
        ))}
      </div>
    </>
  );
}

export default function MetricsPage({ searchParams }: { searchParams: Search }) {
  return (
    <main className="page">
      <div>
        <h1>Metrics</h1>
        <p className="muted" style={{ margin: "4px 0 0" }}>
          Live lab signals. Calm charts stay gray; a chart turns colour only when it crosses its alert threshold.
        </p>
      </div>
      <Suspense fallback={<p>Loading metrics…</p>}>
        <MetricsView searchParams={searchParams} />
      </Suspense>
    </main>
  );
}
