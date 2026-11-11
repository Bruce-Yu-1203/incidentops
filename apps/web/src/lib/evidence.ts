// Helpers for showing evidence citations outside the investigation page.

import type { RunSummary } from "./incidents";

/** Investigations and re-investigations, oldest first; chat turns are left out (ADR-023). */
export function formalRuns(runs: readonly RunSummary[]): RunSummary[] {
  return [...runs]
    .filter((r) => r.kind !== "chat")
    .sort((a, b) => (a.started_at ?? "").localeCompare(b.started_at ?? ""));
}

/** The run whose evidence a diagnosis cites: the latest completed formal run, else the latest formal run. */
export function diagnosisRun(runs: readonly RunSummary[]): RunSummary | null {
  const byStart = formalRuns(runs);
  return byStart.filter((r) => r.state === "COMPLETED").at(-1) ?? byStart.at(-1) ?? null;
}

/** Deep link to an evidence row on the investigation page, or null for a malformed id. */
export function evidenceHref(runId: string | null, evidenceId: string): string | null {
  if (runId === null || !/^ev_[0-9a-zA-Z]+$/.test(evidenceId)) return null;
  return `/investigations/${runId}#${evidenceId}`;
}

/** Short, still-recognisable form of a long record id: prefix plus last six characters. */
export function shortId(id: string): string {
  const [prefix, rest] = id.includes("_")
    ? [id.slice(0, id.indexOf("_") + 1), id.slice(id.indexOf("_") + 1)]
    : ["", id];
  return rest.length <= 8 ? id : `${prefix}…${rest.slice(-6)}`;
}

export const CONFIDENCE_LEVELS = ["low", "medium", "high"] as const;
