// View models for the investigation page: trajectory steps and the evidence
// graph that links a diagnosis to the records it cites.

import type { Diagnosis } from "./incidents";
import type { EvidenceView, StepView } from "./investigations";
import { type Tone, toneOf } from "./states";
import { formatDuration } from "./timeline";

export type StepRow = {
  id: string;
  kind: string;
  title: string;
  detail: string;
  /** Redacted tool arguments as stored by the trajectory recorder, if any. */
  args: string | null;
  tone: Tone;
  duration: string | null;
  /** The gateway refused or rejected the call; shown highlighted. */
  blocked: boolean;
};

const str = (v: unknown) => (typeof v === "string" ? v : null);

export function stepRow(step: StepView): StepRow {
  const m = step.metadata;
  const duration = step.finished_at
    ? formatDuration(new Date(step.finished_at).getTime() - new Date(step.started_at).getTime())
    : null;
  const base = { id: step.id, kind: step.kind, duration };
  if (step.kind === "tool_call" || str(m.tool)) {
    const tone = toneOf("tool", step.status);
    const error = str(m.error_code);
    const evidence = str(m.evidence_id);
    return {
      ...base,
      title: str(m.tool) ?? "tool call",
      detail: [step.status, error, evidence ? `→ ${evidence}` : null].filter(Boolean).join(" · "),
      args:
        m.arguments === undefined
          ? null
          : typeof m.arguments === "string"
            ? m.arguments
            : JSON.stringify(m.arguments),
      tone,
      blocked: tone === "danger",
    };
  }
  if (Array.isArray(m.tool_calls)) {
    return {
      ...base,
      title: "model call",
      detail: `requested ${m.tool_calls.join(", ") || "no tools"}`,
      args: null,
      tone: toneOf("step", step.status),
      blocked: false,
    };
  }
  if (str(m.root_cause_id)) {
    return {
      ...base,
      title: "diagnosis",
      detail: str(m.root_cause_id) ?? "",
      args: null,
      tone: toneOf("step", step.status),
      blocked: false,
    };
  }
  return {
    ...base,
    title: step.kind.replace(/_/g, " "),
    detail: str(m.error) ?? step.status,
    args: null,
    tone: toneOf("step", step.status),
    blocked: false,
  };
}

export type GraphNode = { id: string; evidence: EvidenceView | null };

export type EvidenceGraph = {
  supporting: GraphNode[];
  contradicting: GraphNode[];
  /** Evidence the run collected but the diagnosis does not cite. */
  uncited: EvidenceView[];
};

/** Splits evidence by how the diagnosis uses it; a cited id with no record stays visible as missing. */
export function evidenceGraph(
  diagnosis: Pick<Diagnosis, "supporting_evidence_ids" | "contradicting_evidence_ids"> | null,
  evidence: readonly EvidenceView[],
): EvidenceGraph {
  const byId = new Map(evidence.map((e) => [e.id, e]));
  const supportingIds = diagnosis?.supporting_evidence_ids ?? [];
  const contradictingIds = diagnosis?.contradicting_evidence_ids ?? [];
  const cited = new Set([...supportingIds, ...contradictingIds]);
  const node = (id: string): GraphNode => ({ id, evidence: byId.get(id) ?? null });
  return {
    supporting: supportingIds.map(node),
    contradicting: contradictingIds.map(node),
    uncited: evidence.filter((e) => !cited.has(e.id)),
  };
}
