import { describe, expect, it } from "vitest";

import { apiBaseUrl, fetchHealth } from "./api";

function fakeFetch(status: number, body: unknown): typeof fetch {
  return (async () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    })) as typeof fetch;
}

describe("apiBaseUrl", () => {
  it("defaults to the local control plane", () => {
    expect(apiBaseUrl({})).toBe("http://127.0.0.1:8000");
  });

  it("normalizes to an origin", () => {
    expect(apiBaseUrl({ INCIDENTOPS_API_URL: "http://api:8000/some/path" })).toBe(
      "http://api:8000",
    );
  });

  it("rejects non-HTTP protocols", () => {
    expect(() => apiBaseUrl({ INCIDENTOPS_API_URL: "file:///etc/passwd" })).toThrow();
  });
});

describe("fetchHealth", () => {
  it("returns validated health data", async () => {
    const result = await fetchHealth(
      "http://x",
      fakeFetch(200, { status: "ok", version: "0.1.0", profile: "dev" }),
    );
    expect(result).toEqual({ ok: true, data: { status: "ok", version: "0.1.0", profile: "dev" } });
  });

  it("reports HTTP errors", async () => {
    expect(await fetchHealth("http://x", fakeFetch(503, {}))).toEqual({
      ok: false,
      error: "HTTP 503",
    });
  });

  it("rejects unexpected shapes", async () => {
    const result = await fetchHealth("http://x", fakeFetch(200, { status: "maybe" }));
    expect(result.ok).toBe(false);
  });

  it("reports network failures", async () => {
    const failing = (async () => {
      throw new Error("connection refused");
    }) as typeof fetch;
    expect(await fetchHealth("http://x", failing)).toEqual({
      ok: false,
      error: "connection refused",
    });
  });
});
