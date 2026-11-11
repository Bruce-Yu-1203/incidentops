"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { formatDisplayTime } from "@/lib/display-time";
import { useDisplayTimeZone } from "@/components/use-display-time-zone";
import { LineChart } from "@/components/line-chart";
import { formatValue, isMetricsView, METRIC_GROUPS, METRIC_RANGES, panelHealth, panelWindows, rangeLabel, type MetricRange, type MetricsView, type PanelView } from "@/lib/metrics";

function MetricCard({ panel, view }: { panel: PanelView; view: MetricsView }) {
  const health = panelHealth(panel, new Date(view.end).getTime(), view.step_seconds);
  const missing = ["No data", "Source unavailable"].includes(health);
  const warning = !["Within range", "Observed"].includes(health);
  return (
    <section className={`chart-card metric-card ${warning ? "metric-card-warning" : ""}`}>
      <div className="metric-card-heading">
        <h3>{panel.title}</h3>
        <span className={`metric-health ${warning ? "metric-health-warning" : ""}`}>{health}</span>
      </div>
      <div className="metric-card-value">
        <strong>{missing ? "—" : formatValue(panel.latest, panel.unit)}</strong>
        <span>{health === "Stale data" ? "last recorded" : "latest sample"}</span>
      </div>
      {missing ? <div className="metric-empty"><strong>{health}</strong><span>{health === "No data" ? "No samples in this time window." : "The telemetry request failed. Retrying while Live is enabled."}</span></div>
        : <LineChart panel={panel} startMs={new Date(view.start).getTime()} endMs={new Date(view.end).getTime()}
          stepSeconds={view.step_seconds} windows={panelWindows(panel.panel_id, view.incidents)} />}
      <footer className="metric-card-footer">
        <span>{panel.threshold !== null
          ? `Threshold ${panel.threshold_direction === "below" ? "below" : "above"} ${panel.unit === "bool" ? panel.threshold : formatValue(panel.threshold, panel.unit)}`
          : "Context metric · no alert threshold"}</span>
        {panelWindows(panel.panel_id, view.incidents).length ? <span>Shaded: related incident</span> : null}
      </footer>
    </section>
  );
}

export function MetricsDashboard({ initial, initialRange }: { initial: MetricsView; initialRange: MetricRange }) {
  const timeZone = useDisplayTimeZone();
  const [view, setView] = useState(initial);
  const [range, setRange] = useState(initialRange);
  const [service, setService] = useState("all");
  const [live, setLive] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<AbortController | null>(null);
  const currentRange = useRef(range);
  const pendingRange = useRef<MetricRange | null>(null);

  const load = useCallback(async (minutes: MetricRange) => {
    if (pending.current && pendingRange.current === minutes) return;
    pending.current?.abort();
    pendingRange.current = minutes;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    try {
      const response = await fetch(`/api/metrics?minutes=${minutes}`, { cache: "no-store", signal: controller.signal });
      const body: unknown = await response.json();
      if (!response.ok || !isMetricsView(body)) throw new Error(response.status === 401 ? "Your session expired. Sign in again." : "Could not update metrics. Previous readings are still shown.");
      if (!controller.signal.aborted) { setView(body); setError(null); }
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Could not update metrics.");
    } finally {
      if (!controller.signal.aborted) { setBusy(false); pending.current = null; }
    }
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (live && document.visibilityState === "visible") void load(currentRange.current);
    }, 15000);
    return () => window.clearInterval(timer);
  }, [live, load]);

  useEffect(() => () => pending.current?.abort(), []);

  useEffect(() => {
    const restore = () => {
      const value = Number(new URLSearchParams(window.location.search).get("range"));
      const next = METRIC_RANGES.find((r) => r === value) ?? 30;
      currentRange.current = next;
      setRange(next);
      void load(next);
    };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, [load]);

  function selectRange(next: MetricRange) {
    currentRange.current = next;
    setRange(next);
    window.history.pushState(null, "", `/metrics?range=${next}`);
    void load(next);
  }

  const warningCount = view.panels.filter((p) => !["Within range", "Observed"].includes(panelHealth(p, new Date(view.end).getTime(), view.step_seconds))).length;
  return (
    <div className="metrics-workspace">
      <div className="metrics-toolbar">
        <div className="metrics-controls">
          <div className="segmented" role="group" aria-label="Time range">
            {METRIC_RANGES.map((minutes) => <button key={minutes} type="button" aria-pressed={minutes === range} onClick={() => selectRange(minutes)}>{rangeLabel(minutes)}</button>)}
          </div>
          <label className="metric-service-filter">Service <select aria-label="Service" value={service} onChange={(e) => setService(e.target.value)}>
            <option value="all">All services</option>
            {METRIC_GROUPS.map((group) => <option key={group.service} value={group.service}>{group.title}</option>)}
          </select></label>
        </div>
        <div className="metrics-controls">
          <button type="button" className={`metric-live-toggle ${live ? "is-live" : ""}`} aria-pressed={live}
            onClick={() => { setLive(!live); if (!live) void load(currentRange.current); }}>
            <span aria-hidden="true" />{live ? "Live · 15s" : "Paused"}
          </button>
          <button type="button" className="btn" disabled={busy} onClick={() => void load(currentRange.current)}>{busy ? "Updating…" : "Refresh"}</button>
          <a className="small" href={view.grafana_dashboard_url} target="_blank" rel="noreferrer">Grafana ↗</a>
        </div>
      </div>
      <div className="metrics-summary" role="status">
        <span>{warningCount ? `${warningCount} signals need attention` : "All signals within range"}</span>
        <span>{formatDisplayTime(view.start, timeZone, "date")} – {formatDisplayTime(view.end, timeZone)} · last successful update {formatDisplayTime(view.end, timeZone)} · {timeZone}{busy ? " · updating" : ""}</span>
      </div>
      {error ? <div className="notice notice-error" role="alert">{error} <button type="button" className="btn" onClick={() => void load(currentRange.current)}>Retry</button></div> : null}
      {METRIC_GROUPS.filter((group) => service === "all" || group.service === service).map((group) => (
        <section className="metric-service" key={group.service} aria-labelledby={`group-${group.service}`}>
          <div className="metric-service-heading"><div><h2 id={`group-${group.service}`}>{group.title}</h2><p>{group.description}</p></div><code>{group.service}</code></div>
          <div className="charts">{group.ids.map((id) => {
            const panel = view.panels.find((p) => p.panel_id === id);
            return panel ? <MetricCard key={id} panel={panel} view={view} /> : null;
          })}</div>
        </section>
      ))}
    </div>
  );
}
