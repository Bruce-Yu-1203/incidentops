import { describe, expect, it, vi } from "vitest";

import { closeIncident, decodeRecheck, encodeRecheck } from "./handling";

const ID = `inc_${"a".repeat(32)}`;

describe("closeIncident", () => {
  it("refuses unknown reasons without calling the API", async () => {
    const fetchImpl = vi.fn();
    const result = await closeIncident("http://api", ID, 2, "BOGUS", undefined, fetchImpl);
    expect(result).toEqual({ ok: false, error: "choose a reason" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("surfaces the API outcome on conflict", async () => {
    const fetchImpl = vi.fn(async () =>
      new Response(JSON.stringify({ detail: { outcome: "version_conflict" } }), { status: 409 }),
    );
    const result = await closeIncident("http://api", ID, 2, "FALSE_ALARM", "note", fetchImpl as unknown as typeof fetch);
    expect(result).toEqual({ ok: false, error: "version_conflict" });
    const [, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ expected_version: 2, disposition: "FALSE_ALARM", note: "note" });
  });
});

describe("recheck encoding", () => {
  it("round-trips a compact summary", () => {
    const encoded = encodeRecheck({
      outcome: "PASSED",
      checks: [{ check_id: "lag", passed: true, observed: "lag 5 -> 3" }, { check_id: "x", passed: null, observed: "no data" }],
      checked_at: "2026-11-11T08:00:00Z",
      based_on: "service_defaults",
      alert_cleared_at: null,
    });
    expect(decodeRecheck(encoded)).toEqual({
      outcome: "PASSED",
      at: "2026-11-11T08:00:00Z",
      checks: [{ passed: true, observed: "lag 5 -> 3" }, { passed: null, observed: "no data" }],
    });
    expect(decodeRecheck("not json")).toBeNull();
  });
});
