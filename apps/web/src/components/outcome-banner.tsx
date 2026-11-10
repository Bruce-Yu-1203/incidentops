import type { Outcome } from "@/lib/outcomes";

import { ToneIcon } from "./state-badge";

export function OutcomeBanner({ outcome, children }: { outcome: Outcome; children?: React.ReactNode }) {
  return (
    <section
      className={`outcome tone-${outcome.tone}`}
      data-outcome={outcome.kind}
      aria-labelledby="outcome-title"
    >
      <span className="outcome-icon">
        <ToneIcon tone={outcome.tone} size={18} />
      </span>
      <div className="stack">
        <span className="outcome-eyebrow">{outcome.eyebrow}</span>
        <strong id="outcome-title" className="outcome-title">
          {outcome.title}
        </strong>
        <span>{outcome.body}</span>
        {children}
        <span className="outcome-next">
          <strong>What to do:</strong> {outcome.nextStep}
        </span>
      </div>
    </section>
  );
}
