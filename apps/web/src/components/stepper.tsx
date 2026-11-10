import type { Lifecycle } from "@/lib/lifecycle";

import { ToneIcon } from "./state-badge";

/** Horizontal step bar for one state machine, with the exits still possible. */
export function Stepper({ lifecycle, label }: { lifecycle: Lifecycle; label: string }) {
  return (
    <div className="stepper">
      <ol aria-label={label}>
        {lifecycle.steps.map((step) => (
          <li
            key={step.label}
            className={`step step-${step.status} tone-${step.tone}`}
            aria-current={step.status === "current" ? "step" : undefined}
          >
            <span className="step-dot">
              {step.status === "done" || step.status === "current" ? (
                <ToneIcon tone={step.status === "done" ? "success" : step.tone} size={11} />
              ) : null}
            </span>
            <span className="step-label">{step.label}</span>
          </li>
        ))}
      </ol>
      {lifecycle.exits.length > 0 ? (
        <p className="subtitle step-exits">
          Possible exits from here: {lifecycle.exits.join(" · ")}
        </p>
      ) : null}
    </div>
  );
}
