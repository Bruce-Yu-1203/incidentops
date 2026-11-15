import { describe, expect, it } from "vitest";

import {
  defaultSelection,
  groupInbox,
  groupPrimaryOf,
  inboxStatus,
  incidentTitle,
  relatedCount,
} from "./inbox";
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

describe("incident groups (ADR-030)", () => {
  const member = (id: string, split = false) => ({
    id,
    service_id: id,
    state: "GROUPED" as const,
    split,
  });

  it("counts unsplit members on the primary only", () => {
    const primary = inc("p", {
      primary_incident_id: "p",
      members: [member("a"), member("b"), member("c", true)],
    });
    expect(relatedCount(primary)).toBe(2);
    expect(relatedCount(inc("a", { primary_incident_id: "p", members: [member("a")] }))).toBe(0);
    expect(relatedCount(base)).toBe(0);
  });

  it("links a grouped member to its primary unless it was split out", () => {
    expect(groupPrimaryOf(inc("a", { state: "GROUPED", primary_incident_id: "p" }))).toBe("p");
    expect(
      groupPrimaryOf(
        inc("a", { primary_incident_id: "p", group_split_at: "2026-11-15T05:00:00Z" }),
      ),
    ).toBeNull();
    expect(groupPrimaryOf(inc("p", { primary_incident_id: "p" }))).toBeNull();
  });

  it("keeps grouped members in the handling list with their own label", () => {
    const inbox = groupInbox([inc("a", { state: "GROUPED", primary_incident_id: "p" })]);
    expect(inbox.handling.map((i) => i.label)).toEqual(["Grouped"]);
  });
});
