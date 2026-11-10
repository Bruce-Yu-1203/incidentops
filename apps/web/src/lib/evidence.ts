// Helpers for showing evidence citations outside the investigation page.

import type { RunSummary } from "./incidents";

/** The run whose evidence a diagnosis cites: the latest completed run, else the latest run. */
export function diagnosisRun(runs: readonly RunSummary[]): RunSummary | null {
  const byStart = [...runs].sort((a, b) => (a.started_at ?? "").localeCompare(b.started_at ?? ""));
  return byStart.filter((r) => r.state === "COMPLETED").at(-1) ?? byStart.at(-1) ?? null;
}

/** Deep link to an evidence row on the investigation page, or null for a malformed id. */
export function evidenceHref(runId: string | null, evidenceId: string): string | null {
  if (runId === null || !/^ev_[0-9a-zA-Z]+$/.test(evidenceId)) return null;
  return `/investigations/${runId}#${evidenceId}`;
}

/** Short, still-recognisable form of a long record id: prefix plus last six characters. */
export function shortId(id: string): string {
  const [prefix, rest] = id.includes("_") ? [id.slice(0, id.indexOf("_") + 1), id.slice(id.indexOf("_") + 1)] : ["", id];
  return rest.length <= 8 ? id : `${prefix}…${rest.slice(-6)}`;
}

export const CONFIDENCE_LEVELS = ["low", "medium", "high"] as const;
