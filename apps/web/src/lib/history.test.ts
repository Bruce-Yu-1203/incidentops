import { describe, expect, it } from "vitest";

import { apiParams, formatSeconds, pageHref, parseHistoryQuery, toCsv, type HistoryRow } from "./history";

describe("parseHistoryQuery", () => {
  it("drops unknown values and defaults to 7 days, page 1", () => {
    const q = parseHistoryQuery({ outcome: "bogus", range: "1y", page: "-3", q: " lag " });
    expect(q).toEqual({ q: "lag", outcome: undefined, service_id: undefined, root_cause_id: undefined, confidence: undefined, range: "7d", page: 1 });
  });

  it("builds API params with a since timestamp", () => {
    const q = parseHistoryQuery({ outcome: "auto_resolved", range: "24h", page: "2" });
    const params = apiParams(q, new Date("2026-11-11T12:00:00Z"));
    expect(params.get("since")).toBe("2026-11-10T12:00:00.000Z");
    expect(params.get("outcome")).toBe("auto_resolved");
    expect(params.get("page")).toBe("2");
  });

  it("keeps filters when paging", () => {
    const q = parseHistoryQuery({ service_id: "lab-api" });
    expect(pageHref(q, { page: 3 })).toBe("/history?service_id=lab-api&page=3");
    expect(pageHref(q, { service_id: undefined })).toBe("/history");
  });
});

describe("formatSeconds", () => {
  it("formats durations", () => {
    expect(formatSeconds(null)).toBe("—");
    expect(formatSeconds(41.2)).toBe("41 s");
    expect(formatSeconds(200)).toBe("3 min 20 s");
    expect(formatSeconds(3900)).toBe("1 h 5 min");
  });
});

describe("toCsv", () => {
  it("quotes and neutralises formulas", () => {
    const row = { id: "inc_1", title: '=HYPERLINK("x")', service_id: "a,b" } as unknown as HistoryRow;
    const [, line] = toCsv([row]).split("\n");
    expect(line.startsWith("inc_1,,\"'=HYPERLINK(\"\"x\"\")\",\"a,b\"")).toBe(true);
  });
});
