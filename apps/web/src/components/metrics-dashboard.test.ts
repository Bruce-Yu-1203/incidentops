import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it } from "vitest";
import { MetricsDashboard } from "./metrics-dashboard";

it("renders identical initial timestamps across server host timezones", () => {
  const initial = {
    start: "2026-11-11T19:30:00Z", end: "2026-11-11T20:00:00Z", step_seconds: 10,
    grafana_dashboard_url: "http://localhost:3000", incidents: [],
    panels: [{ panel_id: "api_cpu", title: "CPU", unit: "cores", latest: 0.1, status: "ok" as const,
      threshold: 0.6, series: [{ name: "cpu", points: [[1794427200, 0.1]] as [number, number][] }] }],
  };
  const previous = process.env.TZ;
  try {
    process.env.TZ = "Pacific/Honolulu";
    const first = renderToStaticMarkup(createElement(MetricsDashboard, { initial, initialRange: 30 }));
    process.env.TZ = "Asia/Tokyo";
    const second = renderToStaticMarkup(createElement(MetricsDashboard, { initial, initialRange: 30 }));
    expect(first).toBe(second);
    expect(first).toContain("UTC");
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
});
