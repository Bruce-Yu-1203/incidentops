"use client";

import { useEffect, useId, useRef, useState } from "react";
import { formatDisplayTime } from "@/lib/display-time";
import { useDisplayTimeZone } from "@/components/use-display-time-zone";
import { chartGeometry, formatValue, type IncidentWindow, type MetricMarker, type PanelView } from "@/lib/metrics";

const COLORS = ["#6554a4", "#168780", "#bd7430"];

/** Responsive SVG plot with axes, discontinuities, event markers and keyboard inspection. */
export function LineChart({ panel, startMs, endMs, windows = [], height = 220, markers = [], stepSeconds, tone }: {
  panel: PanelView;
  startMs: number;
  endMs: number;
  windows?: readonly IncidentWindow[];
  height?: number;
  markers?: readonly MetricMarker[];
  stepSeconds?: number;
  tone?: "warm" | "calm";
}) {
  const timeZone = useDisplayTimeZone();
  const timeLabel = (ms: number) => formatDisplayTime(ms, timeZone, "axis");
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [cursor, setCursor] = useState<number | null>(null);
  const clip = useId().replace(/:/g, "");
  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) setWidth(Math.max(240, entry.contentRect.width));
    });
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  const left = 64, top = 18, bottom = 30, right = 14;
  const plotWidth = Math.max(1, width - left - right);
  const plotHeight = Math.max(1, height - top - bottom);
  const g = chartGeometry(panel, startMs, endMs, windows, plotWidth, plotHeight, stepSeconds);
  const span = Math.max(1, endMs - startMs);
  const x = (ms: number) => ((ms - startMs) / span) * plotWidth;
  const inspectedAt = cursor === null ? null : startMs + cursor * span;
  const readings = inspectedAt === null ? [] : panel.series.map((series) => {
    const point = series.points.reduce<[number, number] | null>((best, p) =>
      Number.isFinite(p[1]) && Math.abs(p[0] * 1000 - inspectedAt) < Math.abs((best?.[0] ?? Infinity) * 1000 - inspectedAt) ? p : best, null);
    const close = point && Math.abs(point[0] * 1000 - inspectedAt) <= Math.max(15, (stepSeconds ?? 10) * 2) * 1000;
    return { name: series.name, value: close ? point[1] : null };
  });
  const visibleMarkers = markers.filter((m) => new Date(m.at).getTime() >= startMs && new Date(m.at).getTime() <= endMs);
  return (
    <div ref={container} className={`metric-plot ${tone === "warm" ? "metric-plot-story" : ""}`}>
      <svg className="chart" viewBox={`0 0 ${width} ${height}`} height={height}
        role="img" tabIndex={0} aria-label={`${panel.title}. Use left and right arrows to inspect values.`}
        onPointerMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          setCursor(Math.max(0, Math.min(1, ((event.clientX - rect.left) * width / rect.width - left) / plotWidth)));
        }}
        onPointerLeave={() => setCursor(null)} onFocus={() => setCursor(1)} onBlur={() => setCursor(null)}
        onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          setCursor((previous) => event.key === "Home" ? 0 : event.key === "End" ? 1
            : Math.max(0, Math.min(1, (previous ?? 1) + (event.key === "ArrowRight" ? 1 : -1) / 60)));
        }}>
        <defs><clipPath id={clip}><rect width={plotWidth} height={plotHeight} /></clipPath></defs>
        {[0, 0.5, 1].map((fraction) => {
          const py = top + fraction * plotHeight;
          const value = g.max - fraction * (g.max - g.min);
          return <g key={fraction}>
            <line x1={left} x2={width - right} y1={py} y2={py} className="metric-gridline" />
            <text x={left - 9} y={py + 4} textAnchor="end" className="metric-axis">{panel.unit === "bool" ? value.toFixed(1) : formatValue(value, panel.unit)}</text>
          </g>;
        })}
        <g transform={`translate(${left},${top})`} clipPath={`url(#${clip})`}>
          {g.bands.map((band) => <rect key={band.id} x={band.x} width={band.width} height={plotHeight} className="metric-incident-band" />)}
          {g.thresholdY !== null ? <line x1={0} x2={plotWidth} y1={g.thresholdY} y2={g.thresholdY} className="metric-threshold" /> : null}
          {g.paths.map((path, index) => path.d ? <path key={path.name} d={path.d} fill="none" stroke={COLORS[index % COLORS.length]} strokeWidth={2} vectorEffect="non-scaling-stroke" /> : null)}
          {visibleMarkers.map((marker) => <line key={`${marker.at}-${marker.label}`} x1={x(new Date(marker.at).getTime())} x2={x(new Date(marker.at).getTime())} y1={0} y2={plotHeight} className="metric-event-marker"><title>{`${marker.label} · ${formatDisplayTime(marker.at, timeZone, "date")}`}</title></line>)}
          {cursor !== null ? <line x1={cursor * plotWidth} x2={cursor * plotWidth} y1={0} y2={plotHeight} className="metric-cursor" /> : null}
        </g>
        {[0, 0.5, 1].map((fraction) => <text key={fraction} x={left + fraction * plotWidth} y={height - 7}
          textAnchor={fraction === 0 ? "start" : fraction === 1 ? "end" : "middle"} className="metric-axis">{timeLabel(startMs + fraction * span)}</text>)}
      </svg>
      <div className="metric-readout" aria-live="polite">
        {inspectedAt !== null ? <>
          <time>{timeLabel(inspectedAt)}</time>
          {readings.map((reading, index) => <span key={reading.name}><i style={{ background: COLORS[index % COLORS.length] }} />{reading.name} <strong>{formatValue(reading.value, panel.unit)}</strong></span>)}
        </> : <>
          {panel.series.map((series, index) => <span key={series.name}><i style={{ background: COLORS[index % COLORS.length] }} />{series.name}</span>)}
          <span className="metric-inspect-hint">Hover or focus to inspect</span>
        </>}
      </div>
    </div>
  );
}
