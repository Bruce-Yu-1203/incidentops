# ADR-022: A read-only incident chat answered by the agent worker

- **Status:** Accepted
- **Relates to:** ADR-008, ADR-019, ADR-021, architecture §15

## Context

Operators want to ask questions about one incident in plain language. The API
must not hold model credentials (`workers/credentials.py`), and nothing the
model says may change a system.

## Decision

- One shared chat per incident (`incident_chat_messages`). Any signed-in role
  may ask; questions and answers are append-only.
- The API stores the question and an `answer_chat` outbox job in one
  transaction. The investigation worker, which already holds the read-only
  model identity, claims the job and writes exactly one answer
  (unique `reply_to`).
- The answer is built from recorded incident data only: alerts, diagnosis,
  evidence summaries, actions, verifications and the last few chat turns, all
  redacted and wrapped as untrusted data. The model gets a single
  `answer_question` tool: no live read tools, no `submit_diagnosis`, no
  proposal tool.
- Cited evidence IDs are filtered to the incident's evidence. The model may
  set `suggest_reinvestigation`; it never starts one.
- With the rules baseline (no model) the worker answers with a deterministic
  summary, and on model failure it falls back to the same summary.

## Consequences

Chat cannot widen the agent's authority or the API's credentials. Answers can
be stale when the recorded evidence is old; starting a fresh investigation
from the chat is a separate, human-triggered step that is not built yet.
