import { describe, expect, it } from "vitest";

import { answerFooter, isChatThread, splitCitations } from "./chat";

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
    expect(answerFooter({ records_checked: 9, duration_ms: 2400 })).toBe("checked 9 things · 2.4 s");
    expect(isChatThread({ incident_id: "i", messages: [], pending: false })).toBe(true);
    expect(isChatThread({ messages: [] })).toBe(false);
  });
});
