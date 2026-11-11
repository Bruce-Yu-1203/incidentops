"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

import {
  answerFooter,
  argumentLine,
  basedOnOlderDiagnosis,
  type ChatMessage,
  type ChatStep,
  type ChatThread,
  isChatThread,
  reinvestigationOpen,
  reinvestigationText,
  runningLabel,
  splitCitations,
  SUGGESTED_QUESTIONS,
  toolLabel,
  traceSummary,
} from "@/lib/chat";

import { DISPLACEMENT_MAP_URL, SHOCKWAVE_DURATION_MS, LITE_DURATION_MS, displacementMapReady, prepareDisplacementMap, visibleFilterBounds, glowRange } from "@/lib/chat-motion";

const POLL_MS = 1500;
const POLL_LIMIT = 80;
const DURATION_MS = SHOCKWAVE_DURATION_MS;
const SCALE = 52;

type Mode = "full" | "lite" | "none";

/** Full shockwave needs SVG filters on HTML content, which only Chromium renders well. */
function effectMode(): Mode {
  if (typeof window === "undefined") return "none";
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return "none";
  return "userAgentData" in navigator ? "full" : "lite";
}

type Glow = { el: HTMLDivElement; near: number; far: number };
let warmed = false;
let warming = false;
let preparationGeneration = 0;
let cancelWarmup: () => void = () => {};

let frame = 0;
let restore: () => void = () => {};

const byId = <T extends Element>(id: string) => document.getElementById(id) as T | null;

function configureFilter(page: HTMLElement, filter: SVGFilterElement): DOMRect | null {
  const rect = page.getBoundingClientRect();
  const bounds = visibleFilterBounds(rect, window.innerWidth, window.innerHeight);
  if (!bounds) return null;
  for (const [name, value] of Object.entries(bounds)) filter.setAttribute(name, String(value));
  return rect;
}

/** Paint the same filter at imperceptible strength during idle time to warm its first use. */
async function prepareShockwave() {
  if (effectMode() !== "full" || warmed || warming) return;
  warming = true;
  const generation = preparationGeneration;
  const available = await prepareDisplacementMap();
  if (generation !== preparationGeneration) { warming = false; return; }
  const page = document.querySelector<HTMLElement>(".inbox-layout, .page");
  const filter = byId<SVGFilterElement>("shockfx");
  const channels = ["shock-r", "shock-g", "shock-b"].map((id) => byId<SVGFEDisplacementMapElement>(id));
  if (!available || !page || !filter || frame || !configureFilter(page, filter)) { warming = false; return; }
  const previousFilter = page.style.filter, previousWillChange = page.style.willChange;
  const steps: number[] = [];
  let cancelled = false;
  const cleanup = () => {
    cancelled = true;
    steps.forEach(cancelAnimationFrame);
    page.style.filter = previousFilter;
    page.style.willChange = previousWillChange;
    channels.forEach((channel) => channel?.setAttribute("scale", "0"));
    warming = false;
    cancelWarmup = () => {};
  };
  cancelWarmup = cleanup;
  channels.forEach((channel) => channel?.setAttribute("scale", "0.01"));
  page.style.willChange = "filter";
  steps.push(requestAnimationFrame(() => {
    if (cancelled) return;
    page.style.filter = "url(#shockfx)";
    steps.push(requestAnimationFrame(() => {
      if (cancelled) return;
      steps.push(requestAnimationFrame(() => { cleanup(); warmed = true; }));
    }));
  }));
}

/** Preserve the lens and RGB split; ring and highlight layers animate transform/opacity only. */
function playShockwave(origin: DOMRect): number {
  preparationGeneration += 1;
  cancelWarmup();
  const mode = effectMode();
  const ox = origin.left + origin.width / 2, oy = origin.top + origin.height / 2;
  if (mode === "none") return 0;
  if (mode === "lite" || !warmed || !displacementMapReady()) {
    const lite = byId<HTMLDivElement>("lite-ring");
    if (lite) {
      lite.style.left = `${ox}px`;
      lite.style.top = `${oy}px`;
      lite.classList.remove("on");
      frame = requestAnimationFrame(() => { frame = 0; lite.classList.add("on"); });
    }
    return LITE_DURATION_MS;
  }
  const page = document.querySelector<HTMLElement>(".inbox-layout, .page");
  const filter = byId<SVGFilterElement>("shockfx");
  const map = byId<SVGFEImageElement>("shock-map");
  const ring = byId<HTMLDivElement>("shock-ring");
  const [dR, dG, dB] = ["shock-r", "shock-g", "shock-b"].map((id) => byId<SVGFEDisplacementMapElement>(id));
  if (!page || !filter || !map || !ring || !dR || !dG || !dB) return 0;
  cancelAnimationFrame(frame);
  restore();
  const pageRect = configureFilter(page, filter);
  if (!pageRect) return 0;
  const cx = ox - pageRect.left, cy = oy - pageRect.top;
  const w = window.innerWidth, h = window.innerHeight;
  const maxR = Math.hypot(Math.max(ox, w - ox), Math.max(oy, h - oy)) * 1.05;
  // Complete layout reads before inserting any visual layers.
  const bounds = Array.from(page.querySelectorAll<HTMLElement>(
    ".inbox-card, .card, .tabs, .handover, .reason, .run-step, .stat-card, .chart-card, .ev-item",
  )).map((el) => ({ rect: el.getBoundingClientRect(), radius: getComputedStyle(el).borderRadius }))
    .filter(({ rect }) => rect.width > 0 && rect.bottom > 0 && rect.top < h).slice(0, 32);
  const fragment = document.createDocumentFragment();
  const glows: Glow[] = bounds.map(({ rect, radius }) => {
    const el = document.createElement("div");
    el.className = "shock-highlight";
    Object.assign(el.style, { left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px`, borderRadius: radius });
    fragment.appendChild(el);
    return { el, ...glowRange(rect, ox, oy) };
  });
  document.body.appendChild(fragment);
  ring.style.left = `${ox - 128}px`;
  ring.style.top = `${oy - 128}px`;
  const previousFilter = page.style.filter, previousWillChange = page.style.willChange;
  const cleanup = () => {
    cancelAnimationFrame(frame);
    frame = 0;
    page.style.filter = previousFilter;
    page.style.willChange = previousWillChange;
    ring.style.opacity = "0";
    ring.style.willChange = "";
    glows.forEach((g) => g.el.remove());
    window.removeEventListener("scroll", cleanup, true);
    window.removeEventListener("resize", cleanup);
    restore = () => {};
  };
  restore = cleanup;
  window.addEventListener("scroll", cleanup, { capture: true, passive: true });
  window.addEventListener("resize", cleanup);
  page.style.willChange = "filter";
  page.style.filter = "url(#shockfx)";
  ring.style.willChange = "transform, opacity";
  let started: number | null = null;
  const step = (now: number) => {
    // Begin the clock on the first rendered frame, never skip the opening on a cold frame.
    started ??= now;
    const t = Math.min(1, (now - started) / DURATION_MS);
    const radius = 24 + (1 - Math.pow(1 - t, 3)) * maxR;
    const k = Math.pow(1 - t, 1.4);
    const scale = SCALE * k * Math.min(1, t / 0.06);
    map.setAttribute("x", String(cx - radius));
    map.setAttribute("y", String(cy - radius));
    map.setAttribute("width", String(2 * radius));
    map.setAttribute("height", String(2 * radius));
    dR.setAttribute("scale", String(scale * 1.08));
    dG.setAttribute("scale", String(scale));
    dB.setAttribute("scale", String(scale * 0.92));
    ring.style.transform = `scale(${radius / 128})`;
    ring.style.opacity = String(0.6 * k);
    for (const g of glows) g.el.style.opacity = radius > g.near - 40 && radius < g.far + 40 ? String(0.85 * k) : "0";
    if (t < 1) frame = requestAnimationFrame(step);
    else cleanup();
  };
  frame = requestAnimationFrame(step);
  return DURATION_MS;
}

const subscribeNothing = () => () => {};

function StepList({ steps }: { steps: readonly ChatStep[] }) {
  return (
    <ol className="chat-steps">
      {steps.map((step, i) => (
        <li key={i} className={step.status === "SUCCEEDED" ? "" : "chat-step-failed"}>
          <span className="chat-step-tool">{toolLabel(step.tool)}</span>
          {argumentLine(step.arguments) ? <code className="tiny">{argumentLine(step.arguments)}</code> : null}
          {step.summary ? <span className="tiny muted">{step.summary}</span> : null}
          {step.evidence_id ? <span className="chat-tag">saved {step.evidence_id.slice(-6)}</span> : null}
          {step.duration_ms !== null ? <span className="tiny muted">{(step.duration_ms / 1000).toFixed(1)} s</span> : null}
        </li>
      ))}
    </ol>
  );
}

function Answer({
  message,
  incidentId,
  latestDiagnosis,
}: {
  message: ChatMessage;
  incidentId: string;
  latestDiagnosis: string | null | undefined;
}) {
  const steps = message.steps ?? [];
  return (
    <div className="msg msg-assistant">
      {basedOnOlderDiagnosis(message, latestDiagnosis) ? (
        <span className="chat-tag chat-tag-old">Based on an earlier diagnosis</span>
      ) : null}
      {splitCitations(message.content).map((part, i) =>
        part.evidence ? (
          <a key={i} className="cite" href={`/incidents/${incidentId}?tab=evidence#${part.text}`}>
            {part.text}
          </a>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
      {steps.length ? (
        <details className="chat-trace">
          <summary>
            <span className="chat-pill">Chat agent</span> {traceSummary(steps)}
          </summary>
          <StepList steps={steps} />
        </details>
      ) : null}
      <div className="msg-footer">{answerFooter(message)}</div>
    </div>
  );
}

export function ChatDock({
  incidentId,
  title,
  canAsk = true,
}: {
  incidentId: string;
  title: string;
  /** Operators and above may ask (a question can read live systems); viewers only read. */
  canAsk?: boolean;
}) {
  // True only in the browser, where the portal target exists.
  const mounted = useSyncExternalStore(subscribeNothing, () => true, () => false);
  const [open, setOpen] = useState(false);
  const [instant, setInstant] = useState(false);
  const [thread, setThread] = useState<ChatThread | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const pillRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const polls = useRef(0);
  const openingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const prepare = () => { void prepareShockwave(); };
    const idle = "requestIdleCallback" in window ? window.requestIdleCallback(prepare, { timeout: 1000 }) : null;
    const fallback = idle === null ? setTimeout(prepare, 150) : null;
    return () => {
      if (idle !== null) window.cancelIdleCallback(idle);
      if (fallback !== null) clearTimeout(fallback);
      if (openingTimer.current !== null) clearTimeout(openingTimer.current);
      preparationGeneration += 1;
      cancelWarmup();
      cancelAnimationFrame(frame);
      frame = 0;
      restore();
    };
  }, [incidentId]);

  const load = useCallback(async () => {
    try {
      const response = await fetch(`/api/chat/${incidentId}`, { cache: "no-store" });
      const body: unknown = await response.json();
      if (!response.ok) {
        setError(response.status === 401 ? "Sign in to ask questions." : "Could not load the chat.");
        return;
      }
      if (isChatThread(body)) {
        setThread(body);
        setError(null);
      }
    } catch {
      setError("Could not reach the console.");
    } finally {
      setLoading(false);
    }
  }, [incidentId]);

  const show = useCallback(() => {
    setInstant(effectMode() === "none");
    setOpen(true);
    setLoading(true);
    const delay = pillRef.current ? playShockwave(pillRef.current.getBoundingClientRect()) : 0;
    if (openingTimer.current !== null) clearTimeout(openingTimer.current);
    openingTimer.current = setTimeout(() => { openingTimer.current = null; void load(); }, delay);
  }, [load]);

  const close = useCallback(() => {
    setOpen(false);
    if (openingTimer.current !== null) clearTimeout(openingTimer.current);
    openingTimer.current = null;
    preparationGeneration += 1;
    cancelWarmup();
    cancelAnimationFrame(frame);
    frame = 0;
    restore();
    byId<HTMLDivElement>("lite-ring")?.classList.remove("on");
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "j") {
        event.preventDefault();
        if (open) close();
        else show();
      } else if (event.key === "Escape" && open) {
        close();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, show, close]);

  useEffect(() => {
    const busy = Boolean(thread?.pending || thread?.running || reinvestigationOpen(thread?.reinvestigation));
    if (!open || !busy) {
      polls.current = 0;
      return;
    }
    if (polls.current >= POLL_LIMIT) return;
    const timer = setTimeout(() => {
      polls.current += 1;
      void load();
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [open, thread, load]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [thread?.messages.length, thread?.pending, thread?.running?.steps.length]);

  async function reinvestigate(fromMessage: string) {
    if (requesting) return;
    setRequesting(true);
    try {
      const response = await fetch(`/api/chat/${incidentId}/reinvestigate`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ from_message_id: fromMessage }),
      });
      if (!response.ok) {
        setError(
          response.status === 403
            ? "Only operators can start a re-investigation."
            : response.status === 409
              ? "A re-investigation is already running, or the incident is no longer waiting for a person."
              : "The re-investigation was not started.",
        );
      }
      await load();
    } finally {
      setRequesting(false);
    }
  }

  async function ask(question: string) {
    const text = question.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      const response = await fetch(`/api/chat/${incidentId}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: text }),
      });
      if (!response.ok) {
        setError(
          response.status === 401
            ? "Sign in to ask questions."
            : response.status === 403
              ? "Only operators can ask the agent."
              : "The question was not sent.",
        );
      } else {
        setDraft("");
        await load();
      }
    } finally {
      setSending(false);
    }
  }

  const messages = thread?.messages ?? [];
  const lastAnswer = [...messages].reverse().find((m) => m.role === "assistant");

  const dock = (
    <>
      <div className="shock-ring" id="shock-ring" />
      <div className="lite-ring" id="lite-ring" />
      <svg aria-hidden="true" width="0" height="0" style={{ position: "absolute", width: 0, height: 0 }}>
        <defs>
          <filter
            id="shockfx"
            filterUnits="userSpaceOnUse"
            primitiveUnits="userSpaceOnUse"
            colorInterpolationFilters="sRGB"
            x="0"
            y="0"
            width="100"
            height="100"
          >
            <feFlood floodColor="rgb(128,128,128)" result="neutral" />
            <feImage id="shock-map" href={DISPLACEMENT_MAP_URL} x="0" y="0" width="1" height="1" preserveAspectRatio="none" result="ringmap" />
            <feComposite in="ringmap" in2="neutral" operator="over" result="map" />
            {(["dR", "dG", "dB"] as const).map((name, i) => (
              <feDisplacementMap
                key={name}
                id={["shock-r", "shock-g", "shock-b"][i]}
                in="SourceGraphic"
                in2="map"
                scale="0"
                xChannelSelector="R"
                yChannelSelector="G"
                result={name}
              />
            ))}
            <feColorMatrix in="dR" type="matrix" values="1 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 1 0" result="r" />
            <feColorMatrix in="dG" type="matrix" values="0 0 0 0 0 0 1 0 0 0 0 0 0 0 0 0 0 0 1 0" result="g" />
            <feColorMatrix in="dB" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 1 0 0 0 0 0 1 0" result="b" />
            <feComposite in="r" in2="g" operator="arithmetic" k1="0" k2="1" k3="1" k4="0" result="rg" />
            <feComposite in="rg" in2="b" operator="arithmetic" k1="0" k2="1" k3="1" k4="0" />
          </filter>
        </defs>
      </svg>
      <button
        ref={pillRef}
        type="button"
        className={`ask-pill beside-inbox ${open ? "hidden" : ""}`}
        onPointerEnter={() => { void prepareShockwave(); }}
        onFocus={() => { void prepareShockwave(); }}
        onClick={show}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <span className="spark" aria-hidden="true" />
        <span className="grow">Ask about this incident</span>
        <kbd>⌘J</kbd>
      </button>
      <aside
        className={`ask-drawer ${open ? "open" : ""} ${instant ? "instant" : ""}`}
        role="dialog"
        aria-label="Ask about this incident"
        aria-hidden={!open}
      >
        <header>
          <span style={{ display: "flex", flexDirection: "column" }}>
            <strong>Ask about this incident</strong>
            <span className="tiny muted">{title}</span>
          </span>
          <button type="button" className="btn" onClick={close} aria-label="Close chat">
            ✕
          </button>
        </header>
        <p className="ask-banner">
          Read-only. The chat agent reads what was recorded and may check live telemetry; it cannot change anything
          or propose an action. Everyone on this incident sees this chat.
        </p>
        <div className="ask-messages" ref={listRef}>
          {loading && messages.length === 0 ? <span className="typing" role="status">Loading conversation…</span> : null}
          {messages.length === 0 && !thread?.pending && !loading && canAsk ? (
            <div className="suggest">
              {SUGGESTED_QUESTIONS.map((q) => (
                <button key={q} type="button" onClick={() => void ask(q)}>
                  {q}
                </button>
              ))}
            </div>
          ) : null}
          {messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="msg msg-user">
                {m.content}
              </div>
            ) : (
              <Answer key={m.id} message={m} incidentId={incidentId} latestDiagnosis={thread?.latest_diagnosis_id} />
            ),
          )}
          {thread?.pending ? (
            <div className="msg msg-assistant msg-running">
              <span className="typing" role="status">
                {runningLabel(thread.running)}
              </span>
              {thread.running?.steps.length ? <StepList steps={thread.running.steps} /> : null}
            </div>
          ) : null}
          {lastAnswer?.suggest_reinvestigation && !thread?.pending && !reinvestigationOpen(thread?.reinvestigation) ? (
            <div className="reinvestigate">
              <strong>A fresh investigation could help</strong>
              <span className="muted">
                An operator starts it. Any action it proposes still goes through policy and approval.
              </span>
              <span>
                <button
                  type="button"
                  className="btn"
                  disabled={!canAsk || !thread?.can_reinvestigate || requesting}
                  title={
                    !canAsk
                      ? "Only operators can start a re-investigation"
                      : !thread?.can_reinvestigate
                        ? "Available while the incident waits for a person"
                        : undefined
                  }
                  onClick={() => void reinvestigate(lastAnswer.id)}
                >
                  {requesting ? "Starting…" : "Start re-investigation"}
                </button>
              </span>
            </div>
          ) : null}
          {thread?.reinvestigation ? (
            <p className="reinvestigation-status tiny" role="status">
              {reinvestigationText(thread.reinvestigation)}
              {reinvestigationOpen(thread.reinvestigation)
                ? ". The new diagnosis will appear on the Investigation tab."
                : ""}
            </p>
          ) : null}
          {error ? <p className="notice notice-error">{error}</p> : null}
        </div>
        {canAsk ? (
          <form
            className="ask-composer"
            onSubmit={(event) => {
              event.preventDefault();
              void ask(draft);
            }}
          >
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void ask(draft);
                }
              }}
              placeholder="Ask a question…"
              maxLength={2000}
              rows={1}
              aria-label="Your question"
            />
            <button type="submit" className="btn btn-primary" disabled={sending || !draft.trim()}>
              Ask
            </button>
          </form>
        ) : (
          <p className="ask-composer ask-viewer tiny muted">
            You can read this chat. Asking needs an operator role, because a question can query live systems.
          </p>
        )}
      </aside>
    </>
  );

  return mounted ? createPortal(dock, document.body) : null;
}
