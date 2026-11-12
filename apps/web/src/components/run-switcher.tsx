"use client";

import { type ReactNode, useState } from "react";

export type RunOption = { key: string; label: string; chat: boolean };

/**
 * Investigation tab run picker (ADR-023). Formal runs come first and the
 * diagnosis run is shown by default; chat turns sit behind their own option.
 * Panels are rendered on the server and only hidden here.
 */
export function RunSwitcher({
  options,
  initial,
  panels,
}: {
  options: RunOption[];
  initial: string;
  panels: Record<string, ReactNode>;
}) {
  const [selected, setSelected] = useState(initial);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {options.length > 1 ? (
        <div className="run-switcher" role="tablist" aria-label="Runs">
          {options.map((o) => (
            <button
              key={o.key}
              type="button"
              role="tab"
              aria-selected={selected === o.key}
              className={`run-option ${o.chat ? "run-option-chat" : ""} ${selected === o.key ? "on" : ""}`}
              onClick={() => setSelected(o.key)}
            >
              {o.label}
            </button>
          ))}
        </div>
      ) : null}
      {options.map((o) => (
        <div key={o.key} hidden={selected !== o.key}>
          {panels[o.key]}
        </div>
      ))}
    </div>
  );
}

/** Timeline: chat questions and answers stay hidden until asked for. */
export function ChatRowsToggle({ count, children }: { count: number; children: ReactNode }) {
  const [show, setShow] = useState(false);
  return (
    <div className={show ? "show-chat" : ""}>
      {count ? (
        <label className="tiny muted chat-toggle">
          <input type="checkbox" checked={show} onChange={(event) => setShow(event.target.checked)} /> Show chat
          questions ({count})
        </label>
      ) : null}
      {children}
    </div>
  );
}
