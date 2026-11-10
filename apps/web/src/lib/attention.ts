// Groups incidents for the home page: what needs a human now, and list views.

import type { IncidentSummary } from "./incidents";
import { isTerminal } from "./states";

export type AttentionBucket = "awaiting_approval" | "escalated" | "in_progress" | "failed";

export const BUCKET_STATES: Record<AttentionBucket, readonly string[]> = {
  awaiting_approval: ["AWAITING_APPROVAL"],
  escalated: ["ESCALATED", "INSUFFICIENT_EVIDENCE"],
  in_progress: ["RECEIVED", "TRIAGED", "INVESTIGATING", "DIAGNOSED", "EXECUTING", "VERIFYING"],
  failed: ["FAILED"],
};

export type Attention = Record<AttentionBucket, IncidentSummary[]>;

/** Incidents per bucket, oldest first so the longest-waiting one leads. */
export function groupAttention(incidents: readonly IncidentSummary[]): Attention {
  const byAge = [...incidents].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const pick = (bucket: AttentionBucket) =>
    byAge.filter((incident) => BUCKET_STATES[bucket].includes(incident.state));
  return {
    awaiting_approval: pick("awaiting_approval"),
    escalated: pick("escalated"),
    in_progress: pick("in_progress"),
    failed: pick("failed"),
  };
}

export type ListView = "open" | "closed" | "all";

export function parseView(value: string | undefined): ListView {
  return value === "closed" || value === "all" ? value : "open";
}

/** "open" keeps non-terminal incidents plus ESCALATED ones, which still need a person. */
export function filterIncidents(incidents: readonly IncidentSummary[], view: ListView) {
  if (view === "all") return [...incidents];
  const open = (i: IncidentSummary) => !isTerminal("incident", i.state) || i.state === "ESCALATED";
  return incidents.filter((i) => (view === "open" ? open(i) : !open(i)));
}

/** Compact age such as "45 s", "14 min", "3 h" or "2 d". */
export function formatAge(fromIso: string, now: Date): string {
  const seconds = Math.max(0, Math.round((now.getTime() - new Date(fromIso).getTime()) / 1000));
  if (seconds < 60) return `${seconds} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h`;
  return `${Math.floor(seconds / 86400)} d`;
}
