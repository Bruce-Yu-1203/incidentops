// When a page should poll for new state: only while something can still change
// without a human acting on this page.

import type { ActionDetail } from "./actions";
import type { ActionSummary, IncidentSummary } from "./incidents";
import { isTerminal } from "./states";

export const REFRESH_INTERVAL_MS = 5000;

export function incidentIsLive(
  incident: Pick<IncidentSummary, "state">,
  actions: readonly Pick<ActionSummary, "state">[] = [],
): boolean {
  return (
    !isTerminal("incident", incident.state) ||
    actions.some((a) => !isTerminal("action", a.state) && a.state !== "PENDING_APPROVAL")
  );
}

/**
 * A pending approval is not polled (the countdown is local and a refresh could
 * disturb a reason being typed); a succeeded action is polled until verified.
 */
export function actionIsLive(action: Pick<ActionDetail, "state" | "verifications">): boolean {
  if (action.state === "PENDING_APPROVAL") return false;
  if (action.state === "SUCCEEDED") return action.verifications.length === 0;
  return !isTerminal("action", action.state);
}

export function runIsLive(run: { state: string }): boolean {
  return !isTerminal("run", run.state);
}

export function listIsLive(incidents: readonly Pick<IncidentSummary, "state">[]): boolean {
  return incidents.some((i) => !isTerminal("incident", i.state) && i.state !== "AWAITING_APPROVAL");
}
