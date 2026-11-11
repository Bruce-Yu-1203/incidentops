import { describe, expect, it } from "vitest";

import { defaultSelection, groupInbox, inboxStatus, incidentTitle } from "./inbox";
import type { IncidentSummary } from "./incidents";

const base: IncidentSummary = {
  id: "inc_1",
  dedup_key: "k",
  service_id: "s",
  environment: "lab",
  severity: "critical",
  state: "INVESTIGATING",
  version: 1,
  created_at: "2026-11-11T08:00:00Z",
  updated_at: "2026-11-11T08:00:00Z",
};

const inc = (id: string, over: Partial<IncidentSummary>): IncidentSummary => ({
  ...base,
  id,
  ...over,
});

describe("groupInbox", () => {
  it("puts approvals before handovers and leaves closed incidents out", () => {
    const inbox = groupInbox([
      inc("esc", { state: "ESCALATED", needs_human: true, created_at: "2026-11-11T07:00:00Z" }),
      inc("apr", { state: "AWAITING_APPROVAL" }),
      inc("run", { state: "INVESTIGATING" }),
      inc("done", { state: "ESCALATED", needs_human: false, disposition: "FALSE_ALARM" }),
      inc("ok", { state: "RESOLVED" }),
    ]);
    expect(inbox.needsYou.map((i) => i.incident.id)).toEqual(["apr", "esc"]);
    expect(inbox.handling.map((i) => i.incident.id)).toEqual(["run"]);
    expect(defaultSelection(inbox)).toBe("apr");
  });

  it("returns null selection for an empty inbox", () => {
    expect(defaultSelection(groupInbox([]))).toBeNull();
  });
});

describe("inboxStatus", () => {
  it("labels handovers by whether a person still owes a decision", () => {
    expect(inboxStatus(inc("a", { state: "ESCALATED", needs_human: true })).tone).toBe("red");
    expect(inboxStatus(inc("a", { state: "ESCALATED", needs_human: false })).label).toBe(
      "Closed by a person",
    );
    expect(inboxStatus(inc("a", { state: "VERIFYING" })).tone).toBe("amber");
  });
});

describe("incidentTitle", () => {
  it("humanises the alert name", () => {
    expect(incidentTitle({ title: "lab_kafka_consumer_lag_high", dedup_key: "x" })).toBe(
      "Kafka consumer lag high",
    );
    expect(incidentTitle({ title: null, dedup_key: "svc:alert" })).toBe("Svc:alert");
  });
});
