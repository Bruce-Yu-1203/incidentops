// Final incident report (architecture §13, last step of the UI journey):
// one summary assembled from the incident and its actions' read models.

import type { ActionDetail } from "./actions";
import { diagnosisRun } from "./evidence";
import type { IncidentDetail } from "./incidents";
import { type Tone, isTerminal, toneOf } from "./states";
import { formatDuration } from "./timeline";

export type Milestone = { label: string; at: string | null; sinceOpen: string | null };

export type ActionReport = {
  id: string;
  title: string;
  state: string;
  decision: string | null;
  approver: string | null;
  hash: string;
  verification: string | null;
  failedChecks: string[];
};

export type IncidentReport = {
  headline: string;
  tone: Tone;
  final: boolean;
  milestones: Milestone[];
  actions: ActionReport[];
};

const earliest = (values: (string | null | undefined)[]) =>
  values.filter((v): v is string => typeof v === "string").sort()[0] ?? null;

function headline(incident: IncidentDetail, actions: readonly ActionDetail[]): string {
  const cause = incident.diagnosis?.diagnosis.root_cause_id;
  const last = actions.at(-1);
  const verification = last?.verifications.at(-1)?.outcome;
  switch (incident.state) {
    case "RESOLVED":
      return `Resolved${cause ? ` (${cause})` : ""}${last ? ` by ${last.envelope.action_type}` : ""}, confirmed by independent verification.`;
    case "ESCALATED":
      if (verification === "VERIFICATION_FAILED") {
        return "Escalated: the action succeeded but independent verification found the service had not recovered.";
      }
      if (last?.state === "RECONCILING" || last?.state === "ESCALATED") {
        return "Escalated: the outcome of the action could not be confirmed.";
      }
      return `Escalated to a human${incident.escalation_reason ? `: ${incident.escalation_reason}` : "."}`;
    case "DIAGNOSED_ONLY":
      return `Diagnosed${cause ? ` as ${cause}` : ""}; no remediation was proposed.`;
    case "FAILED":
      return "IncidentOps failed while handling this incident.";
    case "CANCELLED":
      return "Cancelled by an authorized user.";
    default:
      return `Still in progress (${incident.state}). This report is provisional.`;
  }
}

export function buildReport(
  incident: IncidentDetail,
  actions: readonly ActionDetail[],
): IncidentReport {
  const opened = new Date(incident.created_at).getTime();
  const since = (at: string | null) =>
    at === null ? null : `+${formatDuration(new Date(at).getTime() - opened)}`;
  const run = diagnosisRun(incident.runs);
  const final = isTerminal("incident", incident.state);
  const points: [string, string | null][] = [
    [
      "Alert received",
      earliest(incident.alert_events.map((a) => a.received_at)) ?? incident.created_at,
    ],
    ["Diagnosis returned", incident.diagnosis ? (run?.finished_at ?? null) : null],
    ["Approval decided", earliest(actions.flatMap((a) => a.decisions.map((d) => d.decided_at)))],
    [
      "Action outcome recorded",
      earliest(actions.flatMap((a) => a.operations.map((o) => o.updated_at))),
    ],
    [
      "Verification finished",
      earliest(actions.flatMap((a) => a.verifications.map((v) => v.window_end))),
    ],
    [final ? `Closed as ${incident.state}` : "Last update", incident.updated_at],
  ];
  return {
    headline: headline(incident, actions),
    tone: toneOf("incident", incident.state),
    final,
    milestones: points.map(([label, at]) => ({ label, at, sinceOpen: since(at) })),
    actions: actions.map((a) => {
      const decision = a.decisions.at(-1) ?? null;
      const verification = a.verifications.at(-1) ?? null;
      return {
        id: a.id,
        title: `${String(a.envelope.action_type)} → ${String(a.envelope.target_id)}`,
        state: a.state,
        decision: decision?.decision ?? null,
        approver: decision?.approver_id ?? null,
        hash: a.action_hash,
        verification: verification?.outcome ?? null,
        failedChecks: (verification?.checks ?? [])
          .filter((c) => c.passed === false)
          .map((c) => `${c.check_id}: ${c.observed}`),
      };
    }),
  };
}

/** Plain duration figures for the report header (null when not reached). */
export function reportDurations(incident: IncidentDetail, report: IncidentReport) {
  const opened = new Date(incident.created_at).getTime();
  const at = (label: string) => report.milestones.find((m) => m.label === label)?.at ?? null;
  const ms = (iso: string | null) => (iso === null ? null : new Date(iso).getTime() - opened);
  const closed = report.final ? new Date(incident.disposed_at ?? incident.updated_at).getTime() - opened : null;
  return {
    toDiagnosis: ms(at("Diagnosis returned")),
    toApproval: ms(at("Approval decided")),
    toRecovery: incident.state === "RESOLVED" ? ms(at("Verification finished")) : null,
    open: closed,
  };
}

/** The report as Markdown, for pasting into a ticket or postmortem. */
export function reportMarkdown(
  incident: IncidentDetail,
  report: IncidentReport,
  title: string,
  notes: readonly { sentence: string; role: string }[] = [],
): string {
  const d = incident.diagnosis?.diagnosis ?? null;
  const lines = [
    `# ${title}${report.final ? "" : " (provisional)"}`,
    "",
    `**Outcome:** ${report.headline}`,
    "",
    `- Incident: \`${incident.id}\``,
    `- Service: ${incident.service_id} · ${incident.environment} · ${incident.severity}`,
    `- Opened: ${new Date(incident.created_at).toISOString()}`,
  ];
  if (incident.disposition) {
    lines.push(`- Closed by ${incident.disposed_by ?? "a person"}: ${incident.disposition}${incident.disposition_note ? ` — ${incident.disposition_note}` : ""}`);
  }
  lines.push("", "## Milestones", "", "| Milestone | Time | Since opened |", "|---|---|---|");
  for (const m of report.milestones) lines.push(`| ${m.label} | ${m.at ?? "—"} | ${m.sinceOpen ?? "—"} |`);
  lines.push("", "## Diagnosis", "");
  if (d) {
    lines.push(`${d.root_cause_id} on \`${d.affected_component}\` (${d.confidence_level} confidence, a hypothesis).`, "", d.summary);
    const forIt = notes.filter((n) => n.role === "supports").map((n) => `- ✓ ${n.sentence}`);
    const against = notes.filter((n) => n.role === "against").map((n) => `- ! ${n.sentence}`);
    if (forIt.length || against.length) lines.push("", ...forIt, ...against);
  } else {
    lines.push("No diagnosis was produced.");
  }
  lines.push("", "## Actions", "");
  if (report.actions.length === 0) lines.push("No action was proposed.");
  for (const a of report.actions) {
    lines.push(
      `- \`${a.title}\` — ${a.state}; ${a.decision ? `${a.decision} by ${a.approver}` : "no decision"}; verification ${a.verification ?? "not run"}; hash \`${a.hash}\``,
    );
    for (const c of a.failedChecks) lines.push(`  - failed: ${c}`);
  }
  const questions = d?.unresolved_questions ?? [];
  if (questions.length) {
    lines.push("", "## Follow-ups", "", ...questions.map((q) => `- [ ] ${q}`));
  }
  return `${lines.join("\n")}\n`;
}
