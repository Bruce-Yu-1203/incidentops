// Incident chat: shapes shared by the route handler and the drawer.

import { toolLabel } from "./vocabulary";

export type ChatStep = {
  tool: string;
  status: string;
  arguments: Record<string, unknown> | string | null;
  summary: string | null;
  evidence_id: string | null;
  duration_ms: number | null;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  author: string;
  content: string;
  reply_to: string | null;
  cited_evidence_ids: string[];
  records_checked: number;
  duration_ms: number | null;
  suggest_reinvestigation: boolean;
  created_at: string;
  run_id?: string | null;
  diagnosis_id?: string | null;
  steps?: ChatStep[];
  new_evidence_ids?: string[];
};

export type RunningTurn = { run_id: string; asked_by: string | null; started_at: string | null; steps: ChatStep[] };

export type Reinvestigation = {
  run_id: string | null;
  state: string;
  requested_by: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type ChatThread = {
  incident_id: string;
  messages: ChatMessage[];
  pending: boolean;
  running?: RunningTurn | null;
  latest_diagnosis_id?: string | null;
  can_reinvestigate?: boolean;
  reinvestigation?: Reinvestigation | null;
};

export function isChatThread(value: unknown): value is ChatThread {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v.incident_id === "string" && Array.isArray(v.messages) && typeof v.pending === "boolean";
}

export const SUGGESTED_QUESTIONS = [
  "Why do you think this is the cause?",
  "What evidence points the other way?",
  "What should I check before approving?",
  "Has this happened before?",
];

/** Split an answer into text and ev_ citations so citations can be rendered as links. */
export function splitCitations(text: string): { text: string; evidence: boolean }[] {
  const parts: { text: string; evidence: boolean }[] = [];
  let last = 0;
  for (const match of text.matchAll(/\bev_[0-9a-f]{6,32}\b/g)) {
    const index = match.index ?? 0;
    if (index > last) parts.push({ text: text.slice(last, index), evidence: false });
    parts.push({ text: match[0], evidence: true });
    last = index + match[0].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last), evidence: false });
  return parts;
}

/** "2 new evidence records saved · 9 records read · 2.4 s · read-only" */
export function answerFooter(
  message: Pick<ChatMessage, "records_checked" | "duration_ms" | "new_evidence_ids">,
): string {
  const parts: string[] = [];
  const found = message.new_evidence_ids?.length ?? 0;
  if (found) parts.push(`${found} new evidence record${found === 1 ? "" : "s"} saved`);
  parts.push(`${message.records_checked} records read`);
  if (message.duration_ms !== null) parts.push(`${(message.duration_ms / 1000).toFixed(1)} s`);
  parts.push("read-only");
  return parts.join(" · ");
}

/** Plain name of a tool: "consumer lag" for get_kafka_consumer_lag. */
export { toolLabel };

/** "3 checks · consumer lag, consumer status, logs · 4.1 s" for the folded trace line. */
export function traceSummary(steps: readonly ChatStep[]): string {
  const checks = steps.length;
  const names = [...new Set(steps.map((s) => toolLabel(s.tool)))];
  const total = steps.reduce((sum, s) => sum + (s.duration_ms ?? 0), 0);
  const time = total ? ` · ${(total / 1000).toFixed(1)} s` : "";
  return `${checks} check${checks === 1 ? "" : "s"} · ${names.join(", ")}${time}`;
}

/** What the running turn is doing right now, for the live status line. */
export function runningLabel(running: RunningTurn | null | undefined): string {
  const last = running?.steps.at(-1);
  if (!last) return "Reading the incident record…";
  return `Checked ${toolLabel(last.tool)}, thinking…`;
}

/** True when an answer was written before the incident's newest diagnosis. */
export function basedOnOlderDiagnosis(message: ChatMessage, latest: string | null | undefined): boolean {
  return message.role === "assistant" && Boolean(latest) && Boolean(message.diagnosis_id) && message.diagnosis_id !== latest;
}

/** Arguments as one short line: {"consumer_group_id":"x"} -> consumer_group_id=x */
export function argumentLine(args: ChatStep["arguments"]): string {
  if (!args) return "";
  if (typeof args === "string") return args;
  return Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" ");
}

const REINVESTIGATION_TEXT: Record<string, string> = {
  QUEUED: "Re-investigation queued",
  RUNNING: "Re-investigation running",
  COMPLETED: "Re-investigation finished",
  BUDGET_EXCEEDED: "Re-investigation ran out of budget",
  FAILED: "Re-investigation failed",
};

export function reinvestigationText(r: Reinvestigation | null | undefined): string | null {
  if (!r) return null;
  const who = r.requested_by ? ` · asked by ${r.requested_by.replace(/^user:/, "")}` : "";
  return `${REINVESTIGATION_TEXT[r.state] ?? r.state.toLowerCase()}${who}`;
}

export function reinvestigationOpen(r: Reinvestigation | null | undefined): boolean {
  return r !== null && r !== undefined && ["QUEUED", "RUNNING"].includes(r.state);
}

/** Server-side read of the chat thread, for the Investigation and Evidence tabs. */
export async function fetchChatThread(
  baseUrl: string,
  incidentId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ChatThread | null> {
  try {
    const response = await fetchImpl(`${baseUrl}/api/v1/incidents/${incidentId}/chat`, { cache: "no-store" });
    if (!response.ok) return null;
    const body: unknown = await response.json();
    return isChatThread(body) ? body : null;
  } catch {
    return null;
  }
}

/** The question each chat run answered, by run id. */
export function questionsByRun(thread: ChatThread | null): Map<string, string> {
  const questions = new Map((thread?.messages ?? []).filter((m) => m.role === "user").map((m) => [m.id, m.content]));
  const byRun = new Map<string, string>();
  for (const m of thread?.messages ?? []) {
    if (m.role === "assistant" && m.run_id && m.reply_to) byRun.set(m.run_id, questions.get(m.reply_to) ?? "");
  }
  if (thread?.running) {
    const answered = new Set(thread.messages.map((m) => m.reply_to));
    const waiting = thread.messages.find((m) => m.role === "user" && !answered.has(m.id));
    if (waiting) byRun.set(thread.running.run_id, waiting.content);
  }
  return byRun;
}
