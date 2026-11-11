"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

import { answerFooter, type ChatMessage, type ChatThread, isChatThread, splitCitations, SUGGESTED_QUESTIONS } from "@/lib/chat";

const POLL_MS = 1500;
const POLL_LIMIT = 80;
const DURATION_MS = 1150;
const SCALE = 52;

type Mode = "full" | "lite" | "none";

/** Full shockwave needs SVG filters on HTML content, which only Chromium renders well. */
function effectMode(): Mode {
  if (typeof window === "undefined") return "none";
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return "none";
  return "userAgentData" in navigator ? "full" : "lite";
}

let displacementMap: string | null = null;

/** Radial displacement map: neutral gray inside, a ring that pushes pixels outward. */
function makeDisplacementMap(): string {
  if (displacementMap) return displacementMap;
  const n = 256;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = n;
  const ctx = canvas.getContext("2d");
  if (!ctx) return "";
  const image = ctx.createImageData(n, n);
  for (let y = 0; y < n; y++) {
    for (let x = 0; x < n; x++) {
      const u = ((x + 0.5) / n) * 2 - 1;
      const v = ((y + 0.5) / n) * 2 - 1;
      const d = Math.hypot(u, v);
      let f = d > 0.62 && d < 1 ? Math.sin((Math.PI * (d - 0.62)) / 0.38) : 0;
      // The inner edge squeezes and the outer edge stretches.
      f *= 0.55 + 0.45 * Math.min(1, Math.max(0, (d - 0.62) / 0.38));
      const dx = d ? u / d : 0;
      const dy = d ? v / d : 0;
      const i = (y * n + x) * 4;
      image.data[i] = 128 - dx * f * 127;
      image.data[i + 1] = 128 - dy * f * 127;
      image.data[i + 2] = 128;
      image.data[i + 3] = 255;
    }
  }
  ctx.putImageData(image, 0, 0);
  displacementMap = canvas.toDataURL("image/png");
  return displacementMap;
}

type Glow = { el: HTMLElement; shadow: string; rect: DOMRect };

let frame = 0;
let restore: () => void = () => {};

const byId = <T extends Element>(id: string) => document.getElementById(id) as T | null;

/** Six layers: lens displacement, RGB split, colour ring, edge stretch, highlights, then the drawer. */
function playShockwave(origin: DOMRect) {
  const mode = effectMode();
  const ox = origin.left + origin.width / 2;
  const oy = origin.top + origin.height / 2;
  if (mode === "none") return;
  if (mode === "lite") {
    const lite = byId<HTMLDivElement>("lite-ring");
    if (!lite) return;
    lite.style.left = `${ox}px`;
    lite.style.top = `${oy}px`;
    lite.classList.remove("on");
    void lite.offsetWidth;
    lite.classList.add("on");
    return;
  }
  const page = document.querySelector<HTMLElement>(".inbox-layout, .page");
  const filter = byId<SVGFilterElement>("shockfx");
  const image = byId<SVGFEImageElement>("shock-map");
  const ring = byId<HTMLDivElement>("shock-ring");
  const [dR, dG, dB] = ["shock-r", "shock-g", "shock-b"].map((id) => byId<SVGFEDisplacementMapElement>(id));
  if (!page || !filter || !image || !ring || !dR || !dG || !dB) return;
  cancelAnimationFrame(frame);
  restore();
  image.setAttribute("href", makeDisplacementMap());
  const pageRect = page.getBoundingClientRect();
  const cx = ox - pageRect.left;
  const cy = oy - pageRect.top;
  const w = window.innerWidth;
  const h = window.innerHeight;
  const maxR = Math.hypot(Math.max(ox, w - ox), Math.max(oy, h - oy)) * 1.05;
  filter.setAttribute("width", String(Math.ceil(pageRect.width)));
  filter.setAttribute("height", String(Math.ceil(pageRect.height)));
  const glows: Glow[] = Array.from(
    page.querySelectorAll<HTMLElement>(
      ".inbox-card, .card, .tabs, .handover, .reason, .run-step, .stat-card, .chart-card, .ev-item",
    ),
  )
    .map((el) => ({ el, shadow: el.style.boxShadow, rect: el.getBoundingClientRect() }))
    .filter(({ rect }) => rect.bottom > 0 && rect.top < h);
  restore = () => {
    page.style.filter = "";
    page.style.willChange = "";
    ring.style.opacity = "0";
    for (const g of glows) g.el.style.boxShadow = g.shadow;
    restore = () => {};
  };
  page.style.willChange = "filter";
  page.style.filter = "url(#shockfx)";
  const t0 = performance.now();
  const step = (now: number) => {
    const t = Math.min(1, (now - t0) / DURATION_MS);
    const eased = 1 - Math.pow(1 - t, 3);
    const radius = 24 + eased * maxR;
    const k = Math.pow(1 - t, 1.4);
    const fadeIn = Math.min(1, t / 0.06);
    image.setAttribute("x", String(cx - radius));
    image.setAttribute("y", String(cy - radius));
    image.setAttribute("width", String(2 * radius));
    image.setAttribute("height", String(2 * radius));
    const scale = SCALE * k * fadeIn;
    // Slightly different strengths per channel give the RGB split (±8 %).
    dR.setAttribute("scale", String(scale * 1.08));
    dG.setAttribute("scale", String(scale));
    dB.setAttribute("scale", String(scale * 0.92));
    ring.style.left = `${ox - radius}px`;
    ring.style.top = `${oy - radius}px`;
    ring.style.width = ring.style.height = `${2 * radius}px`;
    ring.style.opacity = String(0.6 * k * fadeIn);
    for (const g of glows) {
      const r = g.rect;
      const nx = Math.max(r.left, Math.min(ox, r.right));
      const ny = Math.max(r.top, Math.min(oy, r.bottom));
      const near = Math.hypot(ox - nx, oy - ny);
      const far = Math.hypot(Math.max(ox - r.left, r.right - ox), Math.max(oy - r.top, r.bottom - oy));
      const a = radius > near - 40 && radius < far + 40 ? 0.85 * k : 0;
      const halo = `0 0 0 1.5px rgba(167,139,250,${a.toFixed(3)}), 0 0 16px rgba(244,114,182,${(a * 0.45).toFixed(3)})`;
      g.el.style.boxShadow = a > 0.01 ? (g.shadow ? `${g.shadow}, ${halo}` : halo) : g.shadow;
    }
    if (t < 1) frame = requestAnimationFrame(step);
    else restore();
  };
  frame = requestAnimationFrame(step);
}

const subscribeNothing = () => () => {};

function Answer({ message, incidentId }: { message: ChatMessage; incidentId: string }) {
  return (
    <div className="msg msg-assistant">
      {splitCitations(message.content).map((part, i) =>
        part.evidence ? (
          <a key={i} className="cite" href={`/incidents/${incidentId}?tab=evidence#${part.text}`}>
            {part.text}
          </a>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
      <div className="msg-footer">{answerFooter(message)}</div>
    </div>
  );
}

export function ChatDock({ incidentId, title }: { incidentId: string; title: string }) {
  // True only in the browser, where the portal target exists.
  const mounted = useSyncExternalStore(subscribeNothing, () => true, () => false);
  const [open, setOpen] = useState(false);
  const [instant, setInstant] = useState(false);
  const [thread, setThread] = useState<ChatThread | null>(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const pillRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const polls = useRef(0);

  useEffect(() => {
    return () => {
      cancelAnimationFrame(frame);
      restore();
    };
  }, []);

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
    }
  }, [incidentId]);

  const show = useCallback(() => {
    setInstant(effectMode() === "none");
    setOpen(true);
    void load();
    if (pillRef.current) playShockwave(pillRef.current.getBoundingClientRect());
  }, [load]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "j") {
        event.preventDefault();
        if (open) setOpen(false);
        else show();
      } else if (event.key === "Escape" && open) {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, show]);

  useEffect(() => {
    if (!open || !thread?.pending) {
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
  }, [thread?.messages.length, thread?.pending]);

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
        setError(response.status === 401 ? "Sign in to ask questions." : "The question was not sent.");
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
            <feImage id="shock-map" x="0" y="0" width="1" height="1" preserveAspectRatio="none" result="ringmap" />
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
          <button type="button" className="btn" onClick={() => setOpen(false)} aria-label="Close chat">
            ✕
          </button>
        </header>
        <p className="ask-banner">
          Read-only. Answers come from what was recorded for this incident and cannot change anything. Everyone on
          this incident sees this chat.
        </p>
        <div className="ask-messages" ref={listRef}>
          {messages.length === 0 && !thread?.pending ? (
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
              <Answer key={m.id} message={m} incidentId={incidentId} />
            ),
          )}
          {thread?.pending ? <span className="typing">Reading the incident record…</span> : null}
          {lastAnswer?.suggest_reinvestigation && !thread?.pending ? (
            <div className="reinvestigate">
              <strong>A fresh investigation could help</strong>
              <span className="muted">
                An operator starts it. Any action it proposes still goes through policy and approval.
              </span>
              <span>
                <button type="button" className="btn" disabled title="Not available in this version yet">
                  Start re-investigation
                </button>
              </span>
            </div>
          ) : null}
          {error ? <p className="notice notice-error">{error}</p> : null}
        </div>
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
      </aside>
    </>
  );

  return mounted ? createPortal(dock, document.body) : null;
}
