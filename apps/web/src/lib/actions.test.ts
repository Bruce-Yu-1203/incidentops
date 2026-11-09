import { describe, expect, it } from "vitest";

import { fetchAction, sendDecision } from "./actions";

const ID = `act_${"a".repeat(32)}`;
const HASH = `sha256:${"b".repeat(64)}`;

const detail = {
  id: ID,
  incident_id: `inc_${"c".repeat(32)}`,
  state: "PENDING_APPROVAL",
  version: 2,
  reason_code: null,
  action_hash: HASH,
  policy_version: "p1",
  expires_at: "2026-11-09T12:15:00Z",
  envelope: { action_type: "resume_test_consumer" },
  approval: null,
  decisions: [],
  operations: [],
  verifications: [],
};

function recorder(status: number, body: unknown, calls: { url: string; body: unknown }[]): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), body: init?.body ? JSON.parse(String(init.body)) : null });
    return new Response(JSON.stringify(body), { status });
  }) as typeof fetch;
}

describe("fetchAction", () => {
  it("validates the action detail", async () => {
    expect((await fetchAction("http://api", ID, recorder(200, detail, []))).ok).toBe(true);
    const bad = { ...detail, action_hash: "md5:x" };
    expect((await fetchAction("http://api", ID, recorder(200, bad, []))).ok).toBe(false);
    expect((await fetchAction("http://api", "../x", recorder(200, detail, []))).ok).toBe(false);
  });
});

describe("sendDecision", () => {
  it("sends the exact hash and version when approving", async () => {
    const calls: { url: string; body: unknown }[] = [];
    const result = await sendDecision(
      "http://api",
      { actionId: ID, kind: "approve", expectedVersion: 2, actionHash: HASH },
      recorder(200, { outcome: "approved", action_state: "QUEUED" }, calls),
    );
    expect(result).toEqual({ ok: true, data: { outcome: "approved" } });
    expect(calls[0]).toEqual({
      url: `http://api/api/v1/actions/${ID}/approve`,
      body: { expected_version: 2, action_hash: HASH },
    });
  });

  it("surfaces the API refusal outcome", async () => {
    const result = await sendDecision(
      "http://api",
      { actionId: ID, kind: "approve", expectedVersion: 2, actionHash: HASH },
      recorder(409, { detail: { outcome: "hash_mismatch" } }, []),
    );
    expect(result).toEqual({ ok: false, error: "hash_mismatch" });
  });

  it("rejects malformed input before any request", async () => {
    const calls: { url: string; body: unknown }[] = [];
    const f = recorder(200, {}, calls);
    expect((await sendDecision("http://api", { actionId: ID, kind: "approve", expectedVersion: 2 }, f)).ok).toBe(false);
    expect((await sendDecision("http://api", { actionId: ID, kind: "reject", expectedVersion: 0 }, f)).ok).toBe(false);
    expect(calls).toEqual([]);
  });
});
