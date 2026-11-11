import { describe, expect, it } from "vitest";

import type { AlertEventView, TimelineEntry } from "./incidents";
import { actorOf, alertClearedAt, lifecycleRows, mergeAlerts } from "./lifecycle-rows";

const alert = (at: string, status = "firing"): AlertEventView => ({
  id: at,
  status,
  alert_name: "lab_kafka_consumer_lag_high",
  fingerprint: "f",
  starts_at: at,
  received_at: at,
  labels: {},
  annotations: { observed_value: at },
});

describe("lifecycleRows", () => {
  it("keeps one alert row, drops tool calls and highlights the waiting step", () => {
    const timeline: TimelineEntry[] = [
      { at: "1", kind: "alert", summary: "", reference_id: "a1" },
      { at: "2", kind: "alert", summary: "", reference_id: "a2" },
      { at: "3", kind: "tool", summary: "", reference_id: "ev" },
      { at: "4", kind: "audit", summary: "", reference_id: "x", actor: "service:investigation-worker", event_type: "run_started", decision: "RUNNING" },
      { at: "4", kind: "audit", summary: "", reference_id: "h", actor: "service:investigation-worker", event_type: "investigation_started", decision: "INVESTIGATING" },
      { at: "5", kind: "audit", summary: "", reference_id: "y", actor: "service:proposal-evaluator", event_type: "policy_evaluated", decision: "REQUIRES_APPROVAL" },
      { at: "6", kind: "audit", summary: "", reference_id: "a", actor: "user:amy", event_type: "approval_decided", decision: "REJECTED", resource_type: "approval_requests" },
      { at: "6", kind: "audit", summary: "", reference_id: "b", actor: "user:amy", event_type: "approval_decided", decision: "REJECTED", resource_type: "action_proposals" },
    ];
    const rows = lifecycleRows(timeline, "AWAITING_APPROVAL");
    expect(rows.map((r) => r.actor)).toEqual(["Alertmanager", "Agent", "Policy", "Human", "Human"]);
    expect(rows.map((r) => r.text)).toEqual([
      "Fired the alert",
      "Started the investigation",
      "Checked the proposed action: requires approval",
      "Rejected the action",
      "Approve or reject the action",
    ]);
    expect(rows.at(-1)?.waiting).toBe(true);
  });
});

describe("actorOf", () => {
  it("maps services and people", () => {
    expect(actorOf("user:amy", null)).toBe("Human");
    expect(actorOf("service:verifier", null)).toBe("Verifier");
    expect(actorOf("service:executor", null)).toBe("Executor");
  });
});

describe("mergeAlerts", () => {
  it("collapses repeated deliveries and finds the clear time", () => {
    const events = [alert("2026-01-01T00:00:00Z"), alert("2026-01-01T00:01:00Z"), alert("2026-01-01T00:02:00Z", "resolved")];
    const merged = mergeAlerts(events);
    expect(merged.map((m) => [m.status, m.count])).toEqual([["firing", 2], ["resolved", 1]]);
    expect(alertClearedAt(events)).toBe("2026-01-01T00:02:00Z");
    expect(alertClearedAt(events.slice(0, 2))).toBeNull();
  });
});
