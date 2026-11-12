# ADR-023: A separate chat agent with read tools, and human-requested re-investigation

- **Status:** Accepted
- **Supersedes in part:** ADR-022
- **Relates to:** ADR-008, ADR-021, architecture §6, §7, §15

## Context

ADR-022 answered chat questions from recorded data only. People also want to
ask the agent to look at something now ("is the consumer reading again?"),
and to start a new investigation when the chat shows the recorded diagnosis is
wrong. Both must stay read-only and must not open a path around policy and
human approval.

## Decision

### Two agents, one engine

- The **chat agent** (`incidentops/chat/agent.py`) is a separate agent from the
  investigation agent: its own prompt (`chat-agent-v1`), toolset and budget.
- Toolset: the gateway's read tools, `get_evidence` (expand a recorded evidence
  item of this incident, no live query) and `answer_question`. It has no
  `submit_diagnosis` and no proposal tool, so it cannot change a diagnosis or
  lead to an action. It may only *suggest* a re-investigation, with a reason.
- Budget per question: 8 tool calls, 10 model steps, 60 seconds. When it is
  spent, the model is offered only `answer_question` once.
- It shares the engine: the same model adapter, the same fail-closed
  `ToolGateway` (scope checks, redaction, evidence persistence, a trajectory
  step per call) and the same run records. Each question is an
  `investigation_runs` row with `kind='chat'`, so its tool calls are recorded
  and audited like a formal investigation. A chat run never moves the incident.
- A turn is short and is retried whole, so it runs as a bounded loop without a
  LangGraph checkpointer.
- It runs in its own worker (`python -m incidentops.workers.chat`,
  `make chat-run`), on its own `answer_chat` queue, with the same agent identity
  (database login, read token, model key). A question never waits behind a
  long investigation.
- One turn per incident at a time (a partial unique index on open chat runs);
  a second question waits for the first.
- Asking needs an operator role, because a question can query live systems
  and spends model budget. Viewers can read the thread.

### Context and memory

- Context is rebuilt from the database for every turn. Layers: the fixed
  prompt; an incident snapshot (state, latest diagnosis, actions,
  verifications, whether an investigation is running); an evidence index (ID,
  source, observed time, one line, which kind of run found it) instead of full
  snapshots; chat memory; the question. All incident data is wrapped as
  untrusted.
- Chat memory is the message table itself: the last six messages verbatim,
  plus a rolling summary (`incident_chat_summaries`). When more than twelve
  messages follow the last summary, older ones are folded into a new summary
  (written by the model through a `write_summary` tool, or by a deterministic
  fallback). There is no per-session checkpoint, so a worker restart or many
  readers need nothing special.
- Evidence the chat agent gathers is stored as normal evidence under its chat
  run. Every item carries `observed_at`, and the prompt treats older readings
  as history.
- Each answer records `run_id` and the `diagnosis_id` it was based on, so the
  UI can say an answer predates a newer diagnosis.

### Synchronisation with investigations

- There is no direct channel between the agents. The database (diagnoses and
  evidence) is the only one. Chat sees new diagnoses automatically on its next
  turn, and is told when an investigation is running.
- The chat agent never starts an investigation. A person does.

### Re-investigation

- `POST /api/v1/incidents/{id}/reinvestigate` (operator roles) queues a
  `reinvestigate_incident` job. It is allowed only while the incident waits
  for a person (ADR-021: a handover state with no disposition), and only one
  can be open at a time.
- The investigation worker runs the normal investigation graph as a
  `kind='reinvestigation'` run. Its first message adds the previous diagnosis,
  the earlier evidence index (including chat evidence) and the reason, framed
  as untrusted leads to re-check. Only evidence gathered by the run itself can
  be cited.
- The result is a new diagnosis row. The incident state does not change
  (handover states stay terminal) and a proposed action is **not** sent to
  policy; the person handling the incident sees it and acts by hand. Letting a
  re-investigation re-enter the automated action path needs a state-machine
  change and is left for a later decision.
- The full chat transcript is not passed to the re-investigation, so people's
  guesses and text quoted from logs cannot steer it. Only the reason is passed,
  marked as an unverified hypothesis.

### Display

- Chat steps are shown under each answer in the chat drawer. The
  Investigation tab keeps formal runs as the default and lists chat runs
  behind a run switcher. The timeline shows chat questions and answers only on
  request, but always shows when chat added evidence or suggested a
  re-investigation, and the re-investigation request, start and finish.

## Consequences

- Chat answers can include current values, at the cost of live reads. These
  are bounded per question and per incident.
- Re-investigation gives a person a fresh, evidence-backed diagnosis on a
  handed-over incident without weakening the approval path.
- Benchmark and history queries keep counting formal diagnoses; history uses
  the latest diagnosis for content, which may now come from a re-investigation.
