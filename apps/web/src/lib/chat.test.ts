import { describe, expect, it } from "vitest";

import {
  answerFooter,
  argumentLine,
  basedOnOlderDiagnosis,
  type ChatMessage,
  fetchChatThread,
  isChatThread,
  questionsByRun,
  reinvestigationOpen,
  reinvestigationText,
  runningLabel,
  splitCitations,
  toolLabel,
  traceSummary,
} from "./chat";

describe("splitCitations", () => {
  it("marks evidence ids", () => {
    const id = `ev_${"a".repeat(32)}`;
    expect(splitCitations(`see ${id}.`)).toEqual([
      { text: "see ", evidence: false },
      { text: id, evidence: true },
      { text: ".", evidence: false },
    ]);
    expect(splitCitations("plain")).toEqual([{ text: "plain", evidence: false }]);
  });
});

describe("chat helpers", () => {
  it("formats the footer and validates threads", () => {
    expect(answerFooter({ records_checked: 9, duration_ms: 2400 })).toBe("9 records read · 2.4 s · read-only");
    expect(answerFooter({ records_checked: 3, duration_ms: null, new_evidence_ids: ["ev_1", "ev_2"] })).toBe(
      "2 new evidence records saved · 3 records read · read-only",
    );
    expect(isChatThread({ incident_id: "i", messages: [], pending: false })).toBe(true);
    expect(isChatThread({ messages: [] })).toBe(false);
  });
});

const step = (tool: string, duration_ms: number | null = 1000) => ({
  tool,
  status: "SUCCEEDED",
  arguments: null,
  summary: null,
  evidence_id: null,
  duration_ms,
});

describe("chat agent trace", () => {
  it("names tools in plain words", () => {
    expect(toolLabel("get_kafka_consumer_lag")).toBe("consumer lag");
    expect(toolLabel("get_something_new")).toBe("something new");
  });

  it("summarises a folded trace", () => {
    expect(traceSummary([step("get_kafka_consumer_lag", 1500), step("search_logs", 600), step("search_logs", null)])).toBe(
      "3 checks · consumer lag, logs · 2.1 s",
    );
    expect(traceSummary([step("get_evidence", null)])).toBe("1 check · recorded evidence");
  });

  it("describes the running turn", () => {
    expect(runningLabel(null)).toBe("Reading the incident record…");
    expect(runningLabel({ run_id: "r", asked_by: null, started_at: null, steps: [step("search_logs")] })).toBe(
      "Checked logs, thinking…",
    );
  });

  it("flags answers based on an older diagnosis", () => {
    const answer = { role: "assistant", diagnosis_id: "dia_1" } as ChatMessage;
    expect(basedOnOlderDiagnosis(answer, "dia_2")).toBe(true);
    expect(basedOnOlderDiagnosis(answer, "dia_1")).toBe(false);
    expect(basedOnOlderDiagnosis({ ...answer, diagnosis_id: null }, "dia_2")).toBe(false);
    expect(basedOnOlderDiagnosis({ ...answer, role: "user" }, "dia_2")).toBe(false);
  });

  it("prints arguments on one line", () => {
    expect(argumentLine({ consumer_group_id: "orders", window: 5 })).toBe("consumer_group_id=orders window=5");
    expect(argumentLine("raw")).toBe("raw");
    expect(argumentLine(null)).toBe("");
  });

  it("describes a re-investigation", () => {
    const r = { run_id: null, state: "QUEUED", requested_by: "user:amy", started_at: null, finished_at: null };
    expect(reinvestigationText(r)).toBe("Re-investigation queued · asked by amy");
    expect(reinvestigationText({ ...r, state: "COMPLETED", requested_by: null })).toBe("Re-investigation finished");
    expect(reinvestigationText(null)).toBeNull();
    expect(reinvestigationOpen(r)).toBe(true);
    expect(reinvestigationOpen({ ...r, state: "FAILED" })).toBe(false);
    expect(reinvestigationOpen(undefined)).toBe(false);
  });
});

describe("chat thread reads", () => {
  const message = (id: string, role: "user" | "assistant", extra: Partial<ChatMessage> = {}): ChatMessage => ({
    id,
    role,
    author: role === "user" ? "user:amy" : "agent",
    content: `text ${id}`,
    reply_to: null,
    cited_evidence_ids: [],
    records_checked: 0,
    duration_ms: null,
    suggest_reinvestigation: false,
    created_at: "2026-11-11T00:00:00Z",
    ...extra,
  });

  it("maps chat runs to their questions, including the running one", () => {
    const thread = {
      incident_id: "inc_1",
      pending: true,
      messages: [
        message("m1", "user"),
        message("m2", "assistant", { reply_to: "m1", run_id: "run_1" }),
        message("m3", "user"),
      ],
      running: { run_id: "run_2", asked_by: null, started_at: null, steps: [] },
    };
    expect([...questionsByRun(thread)]).toEqual([
      ["run_1", "text m1"],
      ["run_2", "text m3"],
    ]);
    expect(questionsByRun(null).size).toBe(0);
  });

  it("reads the thread or returns null", async () => {
    const ok = (async () => new Response(JSON.stringify({ incident_id: "i", messages: [], pending: false }))) as typeof fetch;
    expect(await fetchChatThread("http://api", "inc_1", ok)).toEqual({ incident_id: "i", messages: [], pending: false });
    const denied = (async () => new Response("{}", { status: 403 })) as typeof fetch;
    expect(await fetchChatThread("http://api", "inc_1", denied)).toBeNull();
    const down = (async () => {
      throw new Error("down");
    }) as typeof fetch;
    expect(await fetchChatThread("http://api", "inc_1", down)).toBeNull();
  });
});
