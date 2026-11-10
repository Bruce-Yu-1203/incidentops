// Visual semantics for the separate state machines (architecture §5).
// Each state maps to one tone so the UI can tell "waiting for a human",
// "outcome unknown" and "known bad outcome" apart at a glance.

export type Tone = "neutral" | "progress" | "attention" | "success" | "danger" | "unknown";

export type Machine = "incident" | "run" | "action" | "approval" | "verification" | "step" | "tool";

const TONES: Record<Machine, Record<string, Tone>> = {
  incident: {
    RECEIVED: "progress",
    TRIAGED: "progress",
    INVESTIGATING: "progress",
    DIAGNOSED: "progress",
    DIAGNOSED_ONLY: "neutral",
    INSUFFICIENT_EVIDENCE: "unknown",
    AWAITING_APPROVAL: "attention",
    EXECUTING: "progress",
    VERIFYING: "progress",
    RESOLVED: "success",
    ESCALATED: "danger",
    FAILED: "danger",
    CANCELLED: "neutral",
  },
  run: {
    QUEUED: "progress",
    RUNNING: "progress",
    COMPLETED: "success",
    FAILED: "danger",
    CANCELLED: "neutral",
    BUDGET_EXCEEDED: "danger",
  },
  action: {
    PROPOSED: "progress",
    POLICY_DENIED: "neutral",
    PENDING_APPROVAL: "attention",
    REJECTED: "neutral",
    EXPIRED: "neutral",
    CANCELLED: "neutral",
    APPROVED: "progress",
    QUEUED: "progress",
    EXECUTING: "progress",
    SUCCEEDED: "success",
    FAILED: "danger",
    RECONCILING: "unknown",
    ESCALATED: "danger",
  },
  approval: {
    PENDING: "attention",
    APPROVED: "success",
    REJECTED: "neutral",
    EXPIRED: "neutral",
    REVOKED: "neutral",
  },
  verification: {
    PASSED: "success",
    VERIFICATION_FAILED: "danger",
    UNKNOWN: "unknown",
  },
  step: {
    RUNNING: "progress",
    SUCCESS: "success",
    SUCCEEDED: "success",
    COMPLETED: "success",
    FAILED: "danger",
    ERROR: "danger",
  },
  tool: {
    SUCCESS: "success",
    DENIED: "danger",
    INVALID: "danger",
    BUDGET_EXCEEDED: "danger",
    TIMEOUT: "unknown",
    ERROR: "danger",
  },
};

/** Tone for a state; unknown states fall back to neutral rather than failing. */
export function toneOf(machine: Machine, state: string): Tone {
  return TONES[machine][state.toUpperCase()] ?? "neutral";
}

const TERMINAL: Partial<Record<Machine, ReadonlySet<string>>> = {
  incident: new Set(["DIAGNOSED_ONLY", "RESOLVED", "ESCALATED", "FAILED", "CANCELLED"]),
  run: new Set(["COMPLETED", "FAILED", "CANCELLED", "BUDGET_EXCEEDED"]),
  action: new Set([
    "POLICY_DENIED",
    "REJECTED",
    "EXPIRED",
    "CANCELLED",
    "SUCCEEDED",
    "FAILED",
    "ESCALATED",
  ]),
};

/** Whether a state is final, i.e. nothing will change it without new input. */
export function isTerminal(machine: "incident" | "run" | "action", state: string): boolean {
  return TERMINAL[machine]?.has(state) ?? false;
}

/** Short human wording for each tone, used next to the colour so it is never colour-only. */
export const TONE_LABEL: Record<Tone, string> = {
  neutral: "closed",
  progress: "in progress",
  attention: "needs a human",
  success: "done",
  danger: "failed",
  unknown: "outcome unknown",
};
