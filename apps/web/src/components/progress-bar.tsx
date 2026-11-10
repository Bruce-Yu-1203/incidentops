import type { Lifecycle } from "@/lib/lifecycle";

/** One segment per lifecycle step; a compact version of the Stepper for tables. */
export function ProgressBar({ lifecycle }: { lifecycle: Lifecycle }) {
  const current = lifecycle.steps.find((s) => s.status === "current");
  const done = lifecycle.steps.filter((s) => s.status === "done").length;
  const label = current
    ? `At ${current.label}, step ${done + 1} of ${lifecycle.steps.length}`
    : `${done} of ${lifecycle.steps.length} steps done`;
  return (
    <span className="progress" role="img" aria-label={label}>
      {lifecycle.steps.map((step) => (
        <span key={step.label} className={`progress-seg step-${step.status} tone-${step.tone}`} />
      ))}
    </span>
  );
}
