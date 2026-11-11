// Timeline & alerts tab: who did what, in order, and the raw alert deliveries.

import type { AlertEventView, TimelineEntry } from "./incidents";

export type Actor =
  | "Alertmanager"
  | "System"
  | "Agent"
  | "Policy"
  | "Human"
  | "Executor"
  | "Verifier"
  | "Chat agent";

export type LifecycleRow = {
  at: string;
  actor: Actor;
  text: string;
  /** A person must act now (approval or handover); highlighted. */
  waiting: boolean;
  reference: string;
  /** A chat question or answer: hidden unless the reader asks for them. */
  chat?: boolean;
};

// Events that repeat what another row already says are hidden.
const HIDDEN = new Set(["investigation_started", "incident_diagnosed", "action_state_changed", "login"]);

const EVENT_TEXT: Record<string, string> = {
  incident_created: "Opened the incident",
  incident_triaged: "Triaged the alert",
  run_started: "Started the investigation",
  run_finished: "Finished the investigation",
  policy_evaluated: "Checked the proposed action",
  approval_requested: "Asked a person to approve",
  incident_assigned: "Took ownership",
  incident_disposed: "Closed the incident",
  incident_rechecked: "Re-checked the metrics",
  reinvestigation_requested: "Asked for a re-investigation",
  reinvestigation_started: "Started a re-investigation",
  reinvestigation_finished: "Finished the re-investigation",
};

const DECISION_TEXT: Record<string, string> = {
  APPROVED: "Approved the action",
  REJECTED: "Rejected the action",
  EXPIRED: "Let the approval expire",
  CANCELLED: "Cancelled the action",
  ESCALATED: "Handed the incident to a person",
  EXECUTING: "Started the approved action",
  VERIFYING: "Started independent verification",
  RESOLVED: "Confirmed recovery and resolved",
  FAILED: "Stopped: IncidentOps failed",
  SUCCEEDED: "Reported the action succeeded",
  RECONCILING: "Could not confirm the action outcome",
  PASSED: "Verification passed",
  VERIFICATION_FAILED: "Verification failed",
  UNKNOWN: "Verification inconclusive",
  DIAGNOSED_ONLY: "Finished without an action",
  INSUFFICIENT_EVIDENCE: "Found the evidence insufficient",
  POLICY_DENIED: "Denied the action",
  QUEUED: "Queued the action",
};

/** Map an audit actor such as "service:verifier" or "user:amy" to a lane. */
export function actorOf(actor: string | null | undefined, eventType: string | null | undefined): Actor {
  const a = actor ?? "";
  if (a.startsWith("user:")) return "Human";
  if (a === "chat-agent" || a.includes("chat-worker")) return "Chat agent";
  if (a.includes("verifier")) return "Verifier";
  if (a.includes("executor")) return "Executor";
  if (a.includes("policy") || a.includes("evaluator") || (eventType ?? "").startsWith("policy")) return "Policy";
  if (a.includes("agent") || a.includes("investigation")) return "Agent";
  return "System";
}

const lower = (v: string) => v.toLowerCase().replace(/_/g, " ");

const clip = (v: string, n = 80) => (v.length > n ? `${v.slice(0, n - 1)}…` : v);

/** Chat entries (ADR-023): questions and answers are optional rows; evidence and suggestions always show. */
function chatRow(entry: TimelineEntry): { actor: Actor; text: string; chat: boolean } | null {
  switch (entry.event_type) {
    case "chat_question":
      return { actor: "Human", text: `Asked: “${clip(entry.summary)}”`, chat: true };
    case "chat_answer":
      return { actor: "Chat agent", text: `Answered (${entry.summary.replace(/^answered /, "")})`, chat: true };
    case "chat_evidence_added": {
      const n = Number(entry.decision ?? 0) || 0;
      return { actor: "Chat agent", text: `Added ${n} evidence record${n === 1 ? "" : "s"} from chat`, chat: false };
    }
    case "chat_suggested_reinvestigation":
      return { actor: "Chat agent", text: "Suggested a re-investigation", chat: false };
    default:
      return null;
  }
}

function auditRow(entry: TimelineEntry): { actor: Actor; text: string } | null {
  const event = entry.event_type ?? entry.summary.split(":")[0].trim();
  const decision = entry.decision ?? entry.summary.split(":")[1]?.trim() ?? "";
  if (HIDDEN.has(event)) return null;
  let actor = actorOf(entry.actor, event);
  if (event === "approval_decided") {
    // One decision is audited on the approval, the action and the incident.
    if (entry.resource_type === "action_proposals") return null;
    if (entry.resource_type === "incidents") actor = "System";
  }
  if (event === "policy_evaluated") return { actor, text: `Checked the proposed action: ${lower(decision)}` };
  if (event === "run_finished" && decision && decision !== "COMPLETED") {
    return { actor, text: `Stopped the investigation (${lower(decision)})` };
  }
  if (EVENT_TEXT[event]) {
    const suffix = ["incident_disposed", "incident_rechecked"].includes(event) && decision ? ` (${lower(decision)})` : "";
    return { actor, text: EVENT_TEXT[event] + suffix };
  }
  return { actor, text: DECISION_TEXT[decision] ?? entry.summary.replace(/_/g, " ") };
}

export function lifecycleRows(
  timeline: readonly TimelineEntry[],
  state: string,
  needsHuman = false,
): LifecycleRow[] {
  const rows: LifecycleRow[] = [];
  let firstAlert = true;
  for (const entry of timeline) {
    if (entry.kind === "tool") continue; // tool calls live in the Investigation tab
    if (entry.kind === "chat") {
      const row = chatRow(entry);
      if (row) rows.push({ at: entry.at, ...row, waiting: false, reference: entry.reference_id });
      continue;
    }
    if (entry.kind === "alert") {
      if (!firstAlert) continue; // repeats are merged into the alerts table
      firstAlert = false;
      rows.push({ at: entry.at, actor: "Alertmanager", text: "Fired the alert", waiting: false, reference: entry.reference_id });
      continue;
    }
    const row = auditRow(entry);
    if (!row) continue;
    const prev = rows.at(-1);
    if (prev && prev.actor === row.actor && prev.text === row.text) continue;
    rows.push({ at: entry.at, ...row, waiting: false, reference: entry.reference_id });
  }
  const waitingText =
    state === "AWAITING_APPROVAL" ? "Approve or reject the action" : needsHuman ? "Decide what happened and close it" : null;
  if (waitingText) {
    rows.push({ at: new Date().toISOString(), actor: "Human", text: waitingText, waiting: true, reference: "waiting" });
  }
  return rows;
}

export type MergedAlert = {
  alert_name: string;
  status: string;
  first: string;
  last: string;
  count: number;
  summary: string | null;
  observed: string | null;
};

/** Repeated deliveries of the same rule and status collapse into one row. */
export function mergeAlerts(events: readonly AlertEventView[]): MergedAlert[] {
  const merged: MergedAlert[] = [];
  for (const e of [...events].sort((a, b) => a.received_at.localeCompare(b.received_at))) {
    const prev = merged.at(-1);
    if (prev && prev.alert_name === e.alert_name && prev.status === e.status) {
      prev.last = e.received_at;
      prev.count += 1;
      prev.observed = e.annotations.observed_value ?? prev.observed;
      continue;
    }
    merged.push({
      alert_name: e.alert_name,
      status: e.status,
      first: e.received_at,
      last: e.received_at,
      count: 1,
      summary: e.annotations.summary ?? null,
      observed: e.annotations.observed_value ?? null,
    });
  }
  return merged;
}

/** When the newest delivery says the alert resolved, its time. */
export function alertClearedAt(events: readonly AlertEventView[]): string | null {
  const latest = [...events].sort((a, b) => a.received_at.localeCompare(b.received_at)).at(-1);
  return latest?.status === "resolved" ? latest.received_at : null;
}
