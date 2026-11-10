// Normalises the incident read model's timeline (alerts, audit events and
// tool calls) into labelled, toned items with relative timing.

import type { TimelineEntry } from "./incidents";
import { type Tone, toneOf } from "./states";

export type TimelineCategory = "alert" | "audit" | "tool";

export type TimelineItem = {
  key: string;
  at: string;
  category: TimelineCategory;
  title: string;
  detail: string;
  tone: Tone;
  /** Time since the first entry, e.g. "+4 s"; empty for the first entry. */
  offset: string;
};

/** Compact duration: "<1 s", "42 s", "2 min 5 s", "1 h 3 min". */
export function formatDuration(ms: number): string {
  const s = Math.round(Math.max(0, ms) / 1000);
  if (s < 1) return "<1 s";
  if (s < 60) return `${s} s`;
  if (s < 3600) return s % 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s / 60} min`;
  const m = Math.floor(s / 60);
  return m % 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m / 60} h`;
}

const AUDIT_TITLES: Record<string, string> = {
  incident_created: "Incident opened",
  policy_evaluated: "Policy evaluated",
  approval_requested: "Approval requested",
  action_state_changed: "Action state changed",
  verification_completed: "Verification completed",
  proposal_denied: "Proposal denied",
};

const humanize = (value: string) => {
  const words = value.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
};

function decisionTone(decision: string): Tone {
  for (const machine of ["verification", "incident", "action"] as const) {
    const tone = toneOf(machine, decision);
    if (tone !== "neutral" || decision === "CANCELLED") return tone;
  }
  return "neutral";
}

function describe(entry: TimelineEntry): Pick<TimelineItem, "title" | "detail" | "tone"> {
  if (entry.kind === "alert") {
    const status = entry.summary.split(" ")[0] ?? "";
    return {
      title: status === "resolved" ? "Alert resolved" : "Alert firing",
      detail: entry.summary,
      tone: status === "resolved" ? "success" : "danger",
    };
  }
  if (entry.kind === "tool") {
    const parts = entry.summary.trim().split(/\s+/);
    const status = parts.length > 1 ? (parts.pop() ?? "") : "";
    return {
      title: `Tool ${parts.join(" ")}`,
      detail: status,
      tone: status ? toneOf("tool", status) : "neutral",
    };
  }
  const [event, ...rest] = entry.summary.split(":");
  const decision = rest.join(":").trim();
  return {
    title: AUDIT_TITLES[event.trim()] ?? humanize(event),
    detail: decision,
    tone: decision ? decisionTone(decision) : "neutral",
  };
}

export function buildTimeline(entries: readonly TimelineEntry[]): TimelineItem[] {
  const sorted = [...entries].sort((a, b) => a.at.localeCompare(b.at));
  const start = sorted.length ? new Date(sorted[0].at).getTime() : 0;
  return sorted.map((entry, i) => ({
    key: `${entry.kind}-${entry.reference_id}-${i}`,
    at: entry.at,
    category: entry.kind === "alert" || entry.kind === "tool" ? entry.kind : "audit",
    offset: i === 0 ? "" : `+${formatDuration(new Date(entry.at).getTime() - start)}`,
    ...describe(entry),
  }));
}
