import { chartGeometry, type IncidentWindow, type PanelView } from "@/lib/metrics";

const COLORS = ["#B45309", "#57534E"];

/** Native SVG line chart; calm charts are gray, charts over threshold get colour. */
export function LineChart({
  panel,
  startMs,
  endMs,
  windows = [],
  height = 140,
  tone,
}: {
  panel: PanelView;
  startMs: number;
  endMs: number;
  windows?: readonly IncidentWindow[];
  height?: number;
  tone?: "warm" | "calm";
}) {
  const g = chartGeometry(panel, startMs, endMs, windows, 600, 160);
  const hot = tone ? tone === "warm" : panel.status === "over";
  const stroke = (i: number) => (hot ? COLORS[i % COLORS.length] : i === 0 ? "#78716C" : "#A8A29E");
  return (
    <svg
      className="chart"
      viewBox="0 0 600 160"
      preserveAspectRatio="none"
      height={height}
      role="img"
      aria-label={`${panel.title} chart`}
    >
      {g.bands.map((b) => (
        <rect key={b.id} x={b.x} y={0} width={b.width} height={160} fill="#7C3AED" fillOpacity={0.07} />
      ))}
      {g.thresholdY !== null ? (
        <line
          x1={0}
          x2={600}
          y1={g.thresholdY}
          y2={g.thresholdY}
          stroke={hot ? "#DC2626" : "#D6D3D1"}
          strokeDasharray="4 4"
          vectorEffect="non-scaling-stroke"
        />
      ) : null}
      {g.paths.map((p, i) =>
        p.d ? (
          <path
            key={p.name}
            d={p.d}
            fill="none"
            stroke={stroke(i)}
            strokeWidth={2.2}
            vectorEffect="non-scaling-stroke"
          />
        ) : null,
      )}
    </svg>
  );
}
