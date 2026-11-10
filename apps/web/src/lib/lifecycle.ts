// Turns a state-machine position into a step bar: the main ("happy") path,
// where the record currently is, and the exit it took if it left that path.

import { type Machine, type Tone, isTerminal, toneOf } from "./states";

export type StepStatus = "done" | "current" | "upcoming" | "skipped";

export type LifecycleStep = { label: string; status: StepStatus; tone: Tone };

/** `positionKnown` is false when an exit is drawn without history saying where it happened. */
export type Lifecycle = { steps: LifecycleStep[]; exits: string[]; positionKnown: boolean };

export const INCIDENT_MAIN_PATH = [
  "RECEIVED",
  "TRIAGED",
  "INVESTIGATING",
  "DIAGNOSED",
  "AWAITING_APPROVAL",
  "EXECUTING",
  "VERIFYING",
  "RESOLVED",
] as const;

const INCIDENT_EXITS: Record<string, string[]> = {
  RECEIVED: ["FAILED", "CANCELLED"],
  TRIAGED: ["FAILED", "CANCELLED"],
  INVESTIGATING: ["INSUFFICIENT_EVIDENCE", "FAILED", "CANCELLED"],
  DIAGNOSED: ["DIAGNOSED_ONLY", "ESCALATED", "CANCELLED"],
  AWAITING_APPROVAL: ["ESCALATED", "CANCELLED"],
  EXECUTING: ["ESCALATED", "FAILED"],
  VERIFYING: ["ESCALATED"],
  INSUFFICIENT_EVIDENCE: ["ESCALATED"],
};

/** Where an off-path state most likely left the main path when history is unknown. */
const INCIDENT_DEFAULT_BRANCH: Record<string, number> = {
  INSUFFICIENT_EVIDENCE: 2,
  DIAGNOSED_ONLY: 3,
};

function walk(
  machine: Machine,
  main: readonly string[],
  state: string,
  branchAfter: number,
): LifecycleStep[] {
  const at = main.indexOf(state);
  if (at === main.length - 1) {
    return main.map((label) => ({ label, status: "done", tone: "success" }));
  }
  if (at >= 0) {
    return main.map((label, i) =>
      i < at
        ? { label, status: "done", tone: "progress" }
        : i === at
          ? { label, status: "current", tone: toneOf(machine, label) }
          : { label, status: "upcoming", tone: "neutral" },
    );
  }
  const steps: LifecycleStep[] = main.slice(0, branchAfter + 1).map((label) => ({
    label,
    status: "done",
    tone: "progress",
  }));
  steps.push({ label: state, status: "current", tone: toneOf(machine, state) });
  for (const label of main.slice(branchAfter + 1)) {
    steps.push({ label, status: "skipped", tone: "neutral" });
  }
  return steps;
}

/** The furthest main-path state seen in a list of past states. */
function furthest(main: readonly string[], visited: readonly string[]): number {
  return visited.reduce((best, s) => Math.max(best, main.indexOf(s)), -1);
}

/**
 * Incident lifecycle. `visited` lists states the incident passed through (from
 * the audit timeline), so an exit such as ESCALATED is drawn where it happened.
 */
export function incidentLifecycle(state: string, visited: readonly string[] = []): Lifecycle {
  const main = INCIDENT_MAIN_PATH;
  let branch = furthest(main, visited);
  const positionKnown =
    main.includes(state as (typeof main)[number]) ||
    branch >= 0 ||
    state in INCIDENT_DEFAULT_BRANCH;
  if (branch < 0) branch = INCIDENT_DEFAULT_BRANCH[state] ?? 0;
  const steps = walk("incident", main, state, branch);
  return {
    steps,
    exits: isTerminal("incident", state) ? [] : (INCIDENT_EXITS[state] ?? []),
    positionKnown,
  };
}

export const ACTION_MAIN_PATH = [
  "PROPOSED",
  "PENDING_APPROVAL",
  "APPROVED",
  "QUEUED",
  "EXECUTING",
  "SUCCEEDED",
] as const;

const ACTION_BRANCH: Record<string, number> = {
  POLICY_DENIED: 0,
  REJECTED: 1,
  EXPIRED: 1,
  RECONCILING: 4,
  FAILED: 4,
  ESCALATED: 4,
};

const ACTION_EXITS: Record<string, string[]> = {
  PROPOSED: ["POLICY_DENIED"],
  PENDING_APPROVAL: ["REJECTED", "EXPIRED", "CANCELLED"],
  APPROVED: ["CANCELLED"],
  QUEUED: ["CANCELLED"],
  EXECUTING: ["FAILED", "RECONCILING"],
  RECONCILING: ["SUCCEEDED", "FAILED", "ESCALATED"],
};

/** The pseudo step after SUCCEEDED: independent verification is a separate record. */
export const VERIFY_STEP = "VERIFIED";

/**
 * Action lifecycle plus the independent verification step. `approved` says
 * whether an approval decision exists, which places a CANCELLED action.
 */
export function actionLifecycle(
  state: string,
  verificationOutcome: string | null,
  approved = false,
): Lifecycle {
  const main = ACTION_MAIN_PATH;
  const branch = state === "CANCELLED" ? (approved ? 2 : 1) : (ACTION_BRANCH[state] ?? 0);
  const steps = walk("action", main, state, branch);
  if (state === "SUCCEEDED") {
    steps.forEach((s, i) => (s.tone = i === steps.length - 1 ? "success" : "progress"));
    if (verificationOutcome === null) {
      steps.push({ label: VERIFY_STEP, status: "current", tone: "progress" });
    } else {
      const tone = toneOf("verification", verificationOutcome);
      steps.push({
        label: verificationOutcome === "PASSED" ? VERIFY_STEP : verificationOutcome,
        status: tone === "success" ? "done" : "current",
        tone,
      });
    }
  } else {
    steps.push({
      label: VERIFY_STEP,
      status:
        steps.some((s) => s.status === "skipped") || isTerminal("action", state)
          ? "skipped"
          : "upcoming",
      tone: "neutral",
    });
  }
  return { steps, exits: ACTION_EXITS[state] ?? [], positionKnown: true };
}

/** Incident states recorded in audit timeline summaries such as "incident_created: RECEIVED". */
export function visitedStates(summaries: readonly string[]): string[] {
  const known = new Set<string>([...INCIDENT_MAIN_PATH, "INSUFFICIENT_EVIDENCE", "DIAGNOSED_ONLY"]);
  return summaries.map((s) => s.split(":").pop()?.trim() ?? "").filter((s) => known.has(s));
}
