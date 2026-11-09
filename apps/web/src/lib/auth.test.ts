import { describe, expect, it } from "vitest";

import { fetchMe, safeNextPath, withSession } from "./auth";

describe("withSession", () => {
  it("adds the session header", async () => {
    let seen: string | null = null;
    const base = (async (_: RequestInfo | URL, init?: RequestInit) => {
      seen = new Headers(init?.headers).get("Authorization");
      return new Response("{}");
    }) as typeof fetch;
    await withSession(base, "tok")("http://api/x");
    expect(seen).toBe("Session tok");
  });

  it("leaves requests untouched without a token", () => {
    expect(withSession(fetch, undefined)).toBe(fetch);
  });
});

describe("fetchMe", () => {
  it("returns the user or null", async () => {
    const ok = (async () =>
      new Response(JSON.stringify({ username: "a", role: "approver" }))) as typeof fetch;
    expect(await fetchMe("http://api", ok)).toEqual({ username: "a", role: "approver" });
    const denied = (async () => new Response("{}", { status: 401 })) as typeof fetch;
    expect(await fetchMe("http://api", denied)).toBeNull();
  });
});

describe("safeNextPath", () => {
  it("only allows relative in-app paths", () => {
    expect(safeNextPath("/actions/x")).toBe("/actions/x");
    expect(safeNextPath("//evil.example")).toBe("/");
    expect(safeNextPath("https://evil.example")).toBe("/");
    expect(safeNextPath(null)).toBe("/");
  });
});
