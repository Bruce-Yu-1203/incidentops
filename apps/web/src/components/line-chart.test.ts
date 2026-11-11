import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { expect, it, vi } from "vitest";
import { LineChart } from "./line-chart";

it("renders event marker titles as one text node for hydration", () => {
  const error = vi.spyOn(console, "error").mockImplementation(() => {});
  try {
    const html = renderToStaticMarkup(createElement(LineChart, {
      panel: { panel_id: "lag", title: "Lag", unit: "messages", latest: 1, status: "ok", threshold: 200, series: [] },
      startMs: 0, endMs: 60000,
      markers: [{ at: new Date(30000).toISOString(), label: "Alert started" }],
    }));
    expect(html).toContain("<title>Alert started · ");
    expect(error).not.toHaveBeenCalled();
  } finally { error.mockRestore(); }
});
