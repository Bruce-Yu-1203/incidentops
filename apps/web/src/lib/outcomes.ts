// Plain-language outcome of an action, keeping "we do not know" (RECONCILING,
// UNKNOWN) apart from "we know and it is bad" (FAILED, VERIFICATION_FAILED).

import type { ActionDetail } from "./actions";
import type { Tone } from "./states";

export type OutcomeKind =
  | "reconciling"
  | "verification_failed"
  | "verification_unknown"
  | "verified"
  | "action_failed"
  | "escalated";

export type Outcome = {
  kind: OutcomeKind;
  tone: Tone;
  eyebrow: string;
  title: string;
  body: string;
  nextStep: string;
};

type OutcomeInput = Pick<ActionDetail, "state" | "verifications">;

export function actionOutcome(action: OutcomeInput): Outcome | null {
  const verification = action.verifications.at(-1)?.outcome ?? null;
  switch (action.state) {
    case "RECONCILING":
      return {
        kind: "reconciling",
        tone: "unknown",
        eyebrow: "RECONCILING · OUTCOME UNKNOWN",
        title: "We do not know yet whether the action took effect",
        body: "The executor lost the answer after dispatch. IncidentOps is querying the provider by action ID and will not send the write again.",
        nextStep: "Usually nothing. If the outcome stays unknown it is escalated to a human.",
      };
    case "FAILED":
      return {
        kind: "action_failed",
        tone: "danger",
        eyebrow: "ACTION FAILED",
        title: "The provider confirmed the action did not succeed",
        body: "The external operation reported a failure. Nothing was retried automatically.",
        nextStep: "Review the execution receipt and the investigation before proposing again.",
      };
    case "ESCALATED":
      return {
        kind: "escalated",
        tone: "danger",
        eyebrow: "ESCALATED",
        title: "This action needs a human to decide what happened",
        body: "Reconciliation could not confirm the outcome, so automation stopped here.",
        nextStep: "Check the target directly and record the outcome on the incident.",
      };
    case "SUCCEEDED":
      if (verification === "VERIFICATION_FAILED") {
        return {
          kind: "verification_failed",
          tone: "danger",
          eyebrow: "VERIFICATION_FAILED",
          title: "The action worked, but the service did not recover",
          body: "The action outcome is confirmed, but independent recovery checks failed for the verification window. The diagnosis was probably incomplete.",
          nextStep: "Re-open the investigation. The action is not retried automatically.",
        };
      }
      if (verification === "UNKNOWN") {
        return {
          kind: "verification_unknown",
          tone: "unknown",
          eyebrow: "VERIFICATION UNKNOWN",
          title: "Recovery could not be confirmed",
          body: "Verification data was missing or stale, so the incident is not marked resolved.",
          nextStep: "Check the recovery metrics in Grafana.",
        };
      }
      if (verification === "PASSED") {
        return {
          kind: "verified",
          tone: "success",
          eyebrow: "VERIFIED",
          title: "Independent checks confirm the service recovered",
          body: "Every recovery check passed for the verification window.",
          nextStep: "Nothing.",
        };
      }
      return null;
    default:
      return null;
  }
}

type Check = ActionDetail["verifications"][number]["checks"][number];

const CHECK_ORDER = (c: Check) => (c.passed === false ? 0 : c.passed === null ? 1 : 2);

/** Failed checks first, then unknown, then passed, keeping order within each group. */
export function sortChecks(checks: readonly Check[]): Check[] {
  return checks
    .map((check, i) => ({ check, i }))
    .sort((a, b) => CHECK_ORDER(a.check) - CHECK_ORDER(b.check) || a.i - b.i)
    .map(({ check }) => check);
}
