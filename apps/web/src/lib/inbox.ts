// Inbox grouping and the colour/label each incident shows in the list.

import type { IncidentSummary } from "./incidents";
import { isTerminal } from "./states";

export type InboxTone = "violet" | "red" | "amber" | "gray" | "green";

export type InboxItem = {
  incident: IncidentSummary;
  label: string;
  tone: InboxTone;
};

export type Inbox = { needsYou: InboxItem[]; handling: InboxItem[] };

const HANDLING_LABEL: Record<string, string> = {
  RECEIVED: "Received",
  TRIAGED: "Triaged",
  INVESTIGATING: "Investigating",
  DIAGNOSED: "Checking policy",
  EXECUTING: "Running action",
  VERIFYING: "Verifying",
  INSUFFICIENT_EVIDENCE: "Needs more evidence",
};

/** The status line an incident shows in the inbox and its accent colour. */
export function inboxStatus(incident: IncidentSummary): { label: string; tone: InboxTone } {
  switch (incident.state) {
    case "AWAITING_APPROVAL":
      return { label: "Needs your approval", tone: "violet" };
    case "ESCALATED":
      return incident.needs_human
        ? { label: "Needs a person", tone: "red" }
        : { label: "Closed by a person", tone: "gray" };
    case "FAILED":
      return incident.needs_human
        ? { label: "IncidentOps failed", tone: "red" }
        : { label: "Closed by a person", tone: "gray" };
    case "DIAGNOSED_ONLY":
      return incident.needs_human
        ? { label: "Diagnosed, no action", tone: "red" }
        : { label: "Closed by a person", tone: "gray" };
    case "VERIFYING":
    case "EXECUTING":
      return { label: HANDLING_LABEL[incident.state], tone: "amber" };
    case "RESOLVED":
      return { label: "Resolved and verified", tone: "green" };
    case "CANCELLED":
      return { label: "Cancelled", tone: "gray" };
    default:
      return { label: HANDLING_LABEL[incident.state] ?? incident.state, tone: "gray" };
  }
}

const item = (incident: IncidentSummary): InboxItem => ({ incident, ...inboxStatus(incident) });

/** "Needs you": approvals first, then handovers; "System is handling": active work. Oldest first. */
export function groupInbox(incidents: readonly IncidentSummary[]): Inbox {
  const byAge = [...incidents].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const approvals = byAge.filter((i) => i.state === "AWAITING_APPROVAL");
  const handovers = byAge.filter((i) => i.needs_human === true);
  const handling = byAge.filter(
    (i) => i.state !== "AWAITING_APPROVAL" && !isTerminal("incident", i.state),
  );
  return { needsYou: [...approvals, ...handovers].map(item), handling: handling.map(item) };
}

/** Which incident the inbox opens when none is selected. */
export function defaultSelection(inbox: Inbox): string | null {
  return inbox.needsYou[0]?.incident.id ?? inbox.handling[0]?.incident.id ?? null;
}

/** A readable title: the alert name, else the dedup key. */
export function incidentTitle(incident: Pick<IncidentSummary, "title" | "dedup_key">): string {
  const raw = incident.title ?? incident.dedup_key;
  return raw.replace(/^lab_/, "").replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
