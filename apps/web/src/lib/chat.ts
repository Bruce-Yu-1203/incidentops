// Incident chat: shapes shared by the route handler and the drawer.

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
};

export type ChatThread = { incident_id: string; messages: ChatMessage[]; pending: boolean };

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

/** "checked 9 things · 2.4 s" */
export function answerFooter(message: Pick<ChatMessage, "records_checked" | "duration_ms">): string {
  const time = message.duration_ms === null ? "" : ` · ${(message.duration_ms / 1000).toFixed(1)} s`;
  return `checked ${message.records_checked} things${time}`;
}
