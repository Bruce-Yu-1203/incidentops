// Plain-language evidence for the Evidence tab and the "why" card.

import type { Diagnosis } from "./incidents";
import type { EvidenceView } from "./investigations";
import { templateLabel } from "./vocabulary";

export type EvidenceRole = "supports" | "against" | "set_aside";

export type EvidenceNote = {
  id: string;
  role: EvidenceRole;
  /** One sentence: the agent's note, else a template built from the record. */
  sentence: string;
  fromAgent: boolean;
  evidence: EvidenceView | null;
};

const SOURCE_NOUN: Record<string, string> = {
  prometheus: "Metric",
  loki: "Logs",
  tempo: "Traces",
  consumer_status: "Consumer status",
  worker_status: "Consumer status",
  controller: "Lab controller",
  deployment_controller: "Lab controller",
};

const firstSentence = (text: string) => {
  const trimmed = text.replace(/\s+/g, " ").trim();
  const cut = trimmed.search(/[.!?](\s|$)/);
  const sentence = cut > 0 ? trimmed.slice(0, cut + 1) : trimmed;
  return sentence.length > 140 ? `${sentence.slice(0, 137)}…` : sentence;
};

/** Fallback sentence when the agent wrote no note for this evidence. */
export function templateSentence(evidence: EvidenceView | null, id: string): string {
  if (!evidence) return `Record ${id} is cited but was not found in this run.`;
  const noun = SOURCE_NOUN[evidence.source] ?? "Record";
  const template = evidence.template_id ? templateLabel(evidence.template_id) : evidence.source;
  const summary = firstSentence(evidence.summary);
  return summary ? `${noun} (${template}): ${summary}` : `${noun} (${template}) was checked.`;
}

export function evidenceNotes(
  diagnosis: Pick<
    Diagnosis,
    "supporting_evidence_ids" | "contradicting_evidence_ids" | "evidence_notes"
  > | null,
  evidence: readonly EvidenceView[],
): EvidenceNote[] {
  const byId = new Map(evidence.map((e) => [e.id, e]));
  const notes = diagnosis?.evidence_notes ?? {};
  const make = (id: string, role: EvidenceRole): EvidenceNote => {
    const record = byId.get(id) ?? null;
    const agent = notes[id]?.trim();
    return {
      id,
      role,
      sentence: agent || templateSentence(record, id),
      fromAgent: Boolean(agent),
      evidence: record,
    };
  };
  const supports = diagnosis?.supporting_evidence_ids ?? [];
  const against = (diagnosis?.contradicting_evidence_ids ?? []).filter((id) => !supports.includes(id));
  const cited = new Set([...supports, ...against]);
  return [
    ...supports.map((id) => make(id, "supports")),
    ...against.map((id) => make(id, "against")),
    ...evidence.filter((e) => !cited.has(e.id)).map((e) => make(e.id, "set_aside")),
  ];
}

export function byRole(notes: readonly EvidenceNote[], role: EvidenceRole): EvidenceNote[] {
  return notes.filter((n) => n.role === role);
}
