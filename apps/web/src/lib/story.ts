// The four-step "Story" view of one incident: what happened, why we think so,
// what we propose, and whether it worked.

import type { ActionDetail } from "./actions";
import type { IncidentDetail } from "./incidents";
import { incidentTitle } from "./inbox";

export type StoryStatus = "done" | "current" | "upcoming" | "failed";

export type StoryStep = { key: "happened" | "why" | "propose" | "worked"; title: string; status: StoryStatus };

export type Story = { headline: string; summary: string; steps: StoryStep[]; worked: "yes" | "no" | "pending" | "none" };

const ACTIVE = new Set(["RECEIVED", "TRIAGED", "INVESTIGATING"]);

/** Whether the recovery step passed, failed, is still pending, or never applied. */
export function recoveryResult(
  incident: Pick<IncidentDetail, "state">,
  action: Pick<ActionDetail, "state" | "verifications"> | null,
): Story["worked"] {
  if (incident.state === "RESOLVED") return "yes";
  const outcome = action?.verifications.at(-1)?.outcome;
  if (outcome === "PASSED") return "yes";
  if (outcome === "VERIFICATION_FAILED" || outcome === "UNKNOWN") return "no";
  if (action && ["FAILED", "ESCALATED", "RECONCILING"].includes(action.state)) return "no";
  if (["ESCALATED", "FAILED"].includes(incident.state) && action) return "no";
  if (!action) return "none";
  return "pending";
}

export function buildStory(
  incident: Pick<IncidentDetail, "state" | "diagnosis" | "actions" | "title" | "dedup_key">,
  action: Pick<ActionDetail, "state" | "verifications"> | null,
): Story {
  const diagnosis = incident.diagnosis?.diagnosis ?? null;
  const proposed = incident.actions.at(-1) ?? null;
  const worked = recoveryResult(incident, action);
  const investigating = ACTIVE.has(incident.state);
  const awaiting = incident.state === "AWAITING_APPROVAL";

  const steps: StoryStep[] = [
    { key: "happened", title: "What happened", status: "done" },
    {
      key: "why",
      title: "Why we think so",
      status: diagnosis ? "done" : investigating ? "current" : "failed",
    },
    {
      key: "propose",
      title: "What we propose",
      status: !diagnosis
        ? "upcoming"
        : awaiting
          ? "current"
          : proposed
            ? ["REJECTED", "EXPIRED", "POLICY_DENIED", "CANCELLED"].includes(proposed.state)
              ? "failed"
              : "done"
            : "done",
    },
    {
      key: "worked",
      title: "Did it work?",
      status:
        worked === "yes"
          ? "done"
          : worked === "no"
            ? "failed"
            : worked === "pending" && !awaiting
              ? "current"
              : "upcoming",
    },
  ];
  const headline = diagnosis
    ? `${diagnosis.root_cause_id.replace(/_/g, " ")} · ${diagnosis.affected_component}`
    : incidentTitle(incident);
  const summary = diagnosis?.summary?.trim()
    ? compactSummary(diagnosis.summary)
    : investigating
      ? "The agent is still looking into this."
      : "No diagnosis was recorded for this incident.";
  return { headline, summary, steps, worked };
}

/** Preview recorded text without inventing or rewriting the agent's findings. */
export function compactSummary(text: string, limit = 220): string {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (normalized.length <= limit) return normalized;
  const prefix = normalized.slice(0, limit - 2);
  const sentenceEnd = /[.!?。！？](?=\s|$)/g;
  const endings = [...prefix.matchAll(sentenceEnd)];
  const end = endings.at(-1)?.index;
  if (end !== undefined && end >= 60) return prefix.slice(0, end + 1);
  const space = prefix.lastIndexOf(" ");
  return `${prefix.slice(0, space >= limit / 2 ? space : prefix.length).trimEnd()}…`;
}
