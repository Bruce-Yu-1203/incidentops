import Link from "next/link";

import { IncidentTabLink } from "@/components/incident-tab-link";
import { IncidentTabs } from "@/components/incident-tabs";
import { AutoRefresh } from "@/components/auto-refresh";
import { LineChart } from "@/components/line-chart";
import { type ActionDetail, fetchAction } from "@/lib/actions";
import { apiBaseUrl } from "@/lib/api";
import { formatAge } from "@/lib/attention";
import { fetchMe } from "@/lib/auth";
import { diagnosisRun, shortId } from "@/lib/evidence";
import { byRole, evidenceNotes, type EvidenceNote } from "@/lib/evidence-notes";
import { decodeRecheck } from "@/lib/handling";
import { incidentTitle, inboxStatus } from "@/lib/inbox";
import { DISPOSITION_LABEL, DISPOSITIONS, fetchIncident, type IncidentDetail } from "@/lib/incidents";
import { fetchRun, type RunDetail } from "@/lib/investigations";
import { alertClearedAt, lifecycleRows, mergeAlerts } from "@/lib/lifecycle-rows";
import { fetchIncidentMetrics, formatValue } from "@/lib/metrics";
import { incidentIsLive } from "@/lib/refresh";
import { sessionFetch } from "@/lib/session";
import { buildStory, type StoryStatus } from "@/lib/story";
import { stepRow } from "@/lib/trajectory";
import { formatDuration } from "@/lib/timeline";
import { LoadError } from "@/app/load-error";

import { ChatDock } from "@/components/chat-dock";
import { assignToMe, closeHandover, recheckNow } from "./handling";

export { parseIncidentTab as parseTab } from "@/lib/incident-tabs";
export type { IncidentTab as TabKey } from "@/lib/incident-tabs";
import type { IncidentTab as TabKey } from "@/lib/incident-tabs";

export type PaneFlash = { done?: string; error?: string; recheck?: string };

const OPERATE = new Set(["operator", "approver", "admin", "administrator"]);

function Dot({ status, n }: { status: StoryStatus; n: number }) {
  const glyph = status === "failed" ? "✕" : String(n);
  return <span className={`dot dot-${status}`}>{glyph}</span>;
}

function Reason({ note }: { note: EvidenceNote }) {
  const against = note.role === "against";
  return (
    <span className={`reason ${against ? "reason-against" : "reason-for"}`}>
      <span className="tag">{against ? "! Against" : "✓ Supports"}</span>
      {note.sentence}
    </span>
  );
}

function confidencePill(level: string | undefined) {
  const tone = level === "high" ? "pill-green" : level === "medium" ? "pill-amber" : "pill-red";
  return <span className={`pill ${tone}`}>{level ?? "no"} confidence · hypothesis</span>;
}

async function StoryChart({ incident }: { incident: IncidentDetail }) {
  const result = await fetchIncidentMetrics(apiBaseUrl(), incident.id, await sessionFetch());
  if (!result.ok) return <p className="small muted">Incident chart unavailable. Reload to retry.</p>;
  const view = result.data;
  const panel = view.panels[0];
  if (!panel) return <p className="small muted">No chart is configured for this alert.</p>;
  const values = panel.series.flatMap((series) => series.points.map(([, value]) => value));
  const peak = values.length ? Math.max(...values) : null;
  return (
    <div className="incident-chart">
      <div className="row between small">
        <strong>{panel.title}</strong>
        <span>Peak {formatValue(peak, panel.unit)}</span>
      </div>
      <p className="tiny muted">
        {view.mode === "historical" ? "Incident replay" : "Incident in progress"} · {new Date(view.start).toLocaleString()} – {new Date(view.end).toLocaleString()}
      </p>
      {panel.status === "unavailable" || values.length === 0 ? (
        <p className="small muted">Historical metrics are unavailable or outside telemetry retention. Current data is not substituted.</p>
      ) : (
        <LineChart panel={panel} startMs={new Date(view.start).getTime()} endMs={new Date(view.end).getTime()} height={200} tone="warm" markers={view.markers} stepSeconds={view.step_seconds} />
      )}
      <div className="tiny muted">
        {view.markers.map((marker) => <span key={`${marker.at}-${marker.label}`} style={{ marginRight: 12 }}>{marker.label} · {new Date(marker.at).toLocaleTimeString()}</span>)}
        <Link href="/metrics">View current metrics →</Link>
      </div>
    </div>
  );
}

function StoryTab({
  incident,
  action,
  notes,
}: {
  incident: IncidentDetail;
  action: ActionDetail | null;
  notes: EvidenceNote[];
}) {
  const story = buildStory(incident, action);
  const d = incident.diagnosis?.diagnosis;
  const first = incident.alert_events[0];
  const latest = incident.alert_events.at(-1);
  const pending = incident.actions.find((a) => a.state === "PENDING_APPROVAL");
  const last = incident.actions.at(-1);
  const proposed = incident.diagnosis?.proposed_action;
  const reasons = [...byRole(notes, "supports").slice(0, 2), ...byRole(notes, "against").slice(0, 1)];
  const verification = action?.verifications.at(-1);
  const [s1, s2, s3, s4] = story.steps;
  return (
    <ol className="story">
      <li>
        <span className="rail">
          <Dot status={s1.status} n={1} />
          <span className="line" />
        </span>
        <div className="body">
          <span className="title">{s1.title}</span>
          <div className="card">
            <StoryChart incident={incident} />
            <p className="small muted" style={{ margin: "8px 0 0" }}>
              Alert: {first?.annotations.summary ?? incidentTitle(incident)}.
              {latest?.annotations.observed_value ? ` Latest observed value: ${latest.annotations.observed_value}.` : ""}
              {incident.alert_events.length > 1 ? ` Delivered ${incident.alert_events.length} times.` : ""}
            </p>
          </div>
        </div>
      </li>
      <li className={s2.status === "upcoming" ? "upcoming" : undefined}>
        <span className="rail">
          <Dot status={s2.status} n={2} />
          <span className="line" />
        </span>
        <div className="body">
          <span className="row">
            <span className="title">{s2.title}</span>
            {d ? confidencePill(d.confidence_level) : null}
          </span>
          {d ? (
            <>
              <p className="small" style={{ margin: 0 }}>
                <strong>{d.root_cause_id.replace(/_/g, " ")}</strong> on <code>{d.affected_component}</code>
              </p>
              {reasons.length > 0 ? (
                <div className="reason-grid">
                  {reasons.map((n) => (
                    <Reason key={n.id} note={n} />
                  ))}
                </div>
              ) : null}
              <Link href={`?tab=investigation`} className="small">
                How the agent got there →
              </Link>
            </>
          ) : (
            <p className="small muted" style={{ margin: 0 }}>
              {s2.status === "current" ? "The agent is collecting evidence now." : "No diagnosis was recorded."}
            </p>
          )}
        </div>
      </li>
      <li className={s3.status === "upcoming" ? "upcoming" : undefined}>
        <span className="rail">
          <Dot status={s3.status} n={3} />
          <span className="line" />
        </span>
        <div className="body">
          <span className="title">{s3.title}</span>
          {last ? (
            <div className={`card row between ${pending ? "card-violet" : ""}`}>
              <span style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
                <span className="mono" style={{ fontSize: 14 }}>
                  {last.action_type} → {last.target_id}
                </span>
                <span className="tiny muted">
                  {pending
                    ? `Policy says: requires approval · expires ${new Date(pending.expires_at).toLocaleTimeString()}`
                    : `Action ${last.state.toLowerCase().replace(/_/g, " ")}`}
                </span>
              </span>
              <Link href={`/actions/${last.id}`} className={pending ? "btn btn-primary" : "btn"}>
                {pending ? "Review & approve" : "See action"}
              </Link>
            </div>
          ) : proposed ? (
            <p className="small" style={{ margin: 0 }}>
              Proposed <code>{proposed.action_type} → {proposed.target_id}</code>; waiting for policy.
            </p>
          ) : (
            <p className="small muted" style={{ margin: 0 }}>
              {d ? "No action proposed. A person decides what to do." : "Nothing to propose yet."}
            </p>
          )}
        </div>
      </li>
      <li className={s4.status === "upcoming" ? "upcoming" : undefined}>
        <span className="rail">
          <Dot status={s4.status} n={4} />
        </span>
        <div className="body">
          <span className="title">
            {s4.title}
            {story.worked === "yes" ? " Yes" : story.worked === "no" ? " No" : ""}
          </span>
          <p className="small" style={{ margin: 0 }}>
            {story.worked === "yes"
              ? "An independent check confirmed the service recovered."
              : story.worked === "no"
                ? verification
                  ? `The independent check did not confirm recovery (${verification.outcome.toLowerCase().replace(/_/g, " ")}). ${verification.checks
                      .filter((c) => c.passed !== true)
                      .map((c) => c.observed)
                      .join("; ")}`
                  : "The action did not complete, so recovery was not confirmed."
                : story.worked === "pending"
                  ? "After the action runs, an independent check watches the metrics."
                  : "No action, so there is nothing to verify."}
          </p>
        </div>
      </li>
    </ol>
  );
}

function Handover({
  incident,
  username,
  canOperate,
  flash,
}: {
  incident: IncidentDetail;
  username: string | null;
  canOperate: boolean;
  flash: PaneFlash;
}) {
  const cleared = alertClearedAt(incident.alert_events);
  const recheck = decodeRecheck(flash.recheck);
  const hidden = (
    <>
      <input type="hidden" name="incident_id" value={incident.id} />
      <input type="hidden" name="expected_version" value={incident.version} />
    </>
  );
  const why =
    incident.state === "FAILED"
      ? "IncidentOps itself failed while handling this incident."
      : incident.state === "DIAGNOSED_ONLY"
        ? "The agent diagnosed it but no safe action was available."
        : `Automation stopped${incident.escalation_reason ? `: ${incident.escalation_reason.replace(/_/g, " ")}` : "."}`;
  return (
    <section className="handover" aria-labelledby="handover-title">
      <div className="row between">
        <h2 id="handover-title">This needs a person</h2>
        <span className="pill pill-red">no automatic action will run</span>
      </div>
      <p className="small" style={{ margin: 0 }}>
        {why}
      </p>
      <div className="row small">
        <span className="muted">Owner</span>
        <strong>{incident.owner_username ?? "Nobody yet"}</strong>
        {canOperate && incident.owner_username !== username ? (
          <form action={assignToMe}>
            {hidden}
            <button type="submit" className="btn">
              Assign to me
            </button>
          </form>
        ) : null}
      </div>
      {cleared ? (
        <p className="notice notice-ok" style={{ margin: 0 }}>
          The alert cleared {formatAge(cleared, new Date())} ago. Re-check, then close it if the service looks fine.
        </p>
      ) : null}
      <div className="row">
        {canOperate ? (
          <form action={recheckNow}>
            {hidden}
            <button type="submit" className="btn">
              Re-check now
            </button>
          </form>
        ) : null}
        <span className="tiny muted">Reads the metrics once. No AI, no action.</span>
      </div>
      {recheck ? (
        <div className={`notice ${recheck.outcome === "PASSED" ? "notice-ok" : "notice-error"}`}>
          <strong>
            {recheck.outcome === "PASSED"
              ? "Looks recovered"
              : recheck.outcome === "UNKNOWN"
                ? "Not enough data to tell"
                : "Still unhealthy"}
          </strong>
          <ul className="checks">
            {recheck.checks.map((c, i) => (
              <li key={i}>
                {c.passed === true ? "✓" : c.passed === false ? "✕" : "?"} {c.observed}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {canOperate ? (
        <form action={closeHandover} className="close-form">
          {hidden}
          <strong className="small">Close this incident</strong>
          <fieldset>
            <legend className="tiny muted" style={{ marginBottom: 6 }}>
              What happened?
            </legend>
            {DISPOSITIONS.map((d, i) => (
              <label key={d} className="choice">
                <input type="radio" name="disposition" value={d} required defaultChecked={i === 0 && Boolean(cleared)} />
                {DISPOSITION_LABEL[d]}
              </label>
            ))}
          </fieldset>
          <textarea name="note" maxLength={2000} placeholder="Note for the record (optional)" />
          <span className="row">
            <button type="submit" className="btn btn-dark">
              Close incident
            </button>
            <span className="tiny muted">Only records your decision in the audit log. Runs nothing.</span>
          </span>
        </form>
      ) : (
        <p className="tiny muted" style={{ margin: 0 }}>
          Operators can assign, re-check and close this incident.
        </p>
      )}
    </section>
  );
}

function stepIcon(title: string, blocked: boolean, kind: string, final: boolean) {
  if (blocked) return "✕";
  if (final) return "★";
  if (title === "model call" || kind === "model_call") return "◆";
  return "●";
}

function InvestigationTab({ run, notes, incident }: { run: RunDetail | null; notes: EvidenceNote[]; incident: IncidentDetail }) {
  if (!run) return <p className="muted">No investigation has run yet.</p>;
  const rows = run.steps.map(stepRow);
  const isFinal = (r: (typeof rows)[number]) => r.title === "diagnosis";
  const duration =
    run.started_at && run.finished_at
      ? formatDuration(new Date(run.finished_at).getTime() - new Date(run.started_at).getTime())
      : "running";
  const supports = byRole(notes, "supports");
  const against = byRole(notes, "against");
  const aside = byRole(notes, "set_aside");
  const total = supports.length + against.length || 1;
  const d = run.diagnosis?.diagnosis ?? incident.diagnosis?.diagnosis ?? null;
  return (
    <div className="stack-lg" style={{ display: "flex", flexDirection: "column", gap: 18 }}>
      <div className="run-summary">
        <span className="cell">
          <span className="tiny muted">Run</span>
          <span className="value">{run.state.toLowerCase()}</span>
        </span>
        <span className="cell">
          <span className="tiny muted">Steps</span>
          <span className="value">{run.step_count}</span>
        </span>
        <span className="cell">
          <span className="tiny muted">Tool calls</span>
          <span className="value">{run.tool_call_count}</span>
        </span>
        <span className="cell">
          <span className="tiny muted">Took</span>
          <span className="value">{duration}</span>
        </span>
        <span className="cell">
          <span className="tiny muted">Model</span>
          <span className="value mono" style={{ fontSize: 13 }}>
            {run.model_id ?? "—"}
          </span>
        </span>
      </div>
      <div className="segments" aria-hidden="true">
        {rows.map((r) => (
          <span
            key={r.id}
            className={r.blocked ? "seg-blocked" : isFinal(r) ? "seg-final" : r.title === "model call" ? "seg-model" : "seg-tool"}
          />
        ))}
      </div>
      <ol className="run-steps">
        {rows.map((r) => {
          const final = isFinal(r);
          const ev = /→ (ev_[0-9a-zA-Z]+)/.exec(r.detail)?.[1];
          return (
            <li key={r.id} id={r.id} className={`run-step ${r.blocked ? "step-blocked" : ""} ${final ? "step-final" : ""}`}>
              <span className="icon">{stepIcon(r.title, r.blocked, r.kind, final)}</span>
              <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                <span className="name">{final ? "submit_diagnosis" : r.title}</span>
                {r.args ? <span className="args">{r.args}</span> : null}
                <span className="tiny">
                  {r.detail.replace(/→ ev_[0-9a-zA-Z]+/, "").replace(/ · $/, "")}
                  {ev ? (
                    <>
                      {" "}
                      <Link href={`?tab=evidence#${ev}`} className="mono">
                        → {shortId(ev)}
                      </Link>
                    </>
                  ) : null}
                </span>
              </span>
              <span className="tiny muted">{r.duration ?? ""}</span>
            </li>
          );
        })}
      </ol>
      {d ? (
        <section className="card why" aria-labelledby="why-title">
          <h3 id="why-title" className="serif" style={{ margin: 0, fontSize: 18 }}>
            Why the agent believes it
          </h3>
          <details id="full-diagnosis" className="full-diagnosis">
            <summary>Full diagnosis</summary>
            <p>{d.summary}</p>
          </details>
          <div className="conclusion">
            {d.root_cause_id.replace(/_/g, " ")} · {d.confidence_level} confidence
          </div>
          <div>
            <div className="ratio" aria-label={`${supports.length} for, ${against.length} against`}>
              <span className="for" style={{ width: `${(supports.length / total) * 100}%` }} />
              <span className="against" style={{ width: `${(against.length / total) * 100}%` }} />
            </div>
            <span className="tiny muted">
              {supports.length} for · {against.length} against
            </span>
          </div>
          <div className="two-col">
            <div>
              <strong className="small" style={{ color: "var(--ok)" }}>
                Points for it
              </strong>
              <ul className="plain-list">
                {supports.slice(0, 3).map((n) => (
                  <li key={n.id}>{n.sentence}</li>
                ))}
              </ul>
              {supports.length > 3 ? <span className="tiny muted">+{supports.length - 3} more</span> : null}
            </div>
            <div>
              <strong className="small" style={{ color: "var(--amber-ink)" }}>
                Points against
              </strong>
              {against.length ? (
                <ul className="plain-list">
                  {against.slice(0, 3).map((n) => (
                    <li key={n.id}>{n.sentence}</li>
                  ))}
                </ul>
              ) : (
                <p className="small muted" style={{ margin: 0 }}>
                  Nothing recorded against it.
                </p>
              )}
              {against.length > 3 ? <span className="tiny muted">+{against.length - 3} more</span> : null}
            </div>
          </div>
          {aside.length ? (
            <p className="small muted" style={{ margin: 0 }}>
              Also checked and ruled out:{" "}
              {aside
                .slice(0, 6)
                .map((n) => n.evidence?.template_id?.replace(/_/g, " ") || n.evidence?.source || shortId(n.id))
                .join(", ")}
              {aside.length > 6 ? ` and ${aside.length - 6} more` : ""}.
            </p>
          ) : null}
          <Link href="?tab=evidence" className="small">
            See all evidence →
          </Link>
        </section>
      ) : null}
    </div>
  );
}

const GROUPS = [
  { role: "supports", title: "Supports the diagnosis" },
  { role: "against", title: "Points the other way" },
  { role: "set_aside", title: "Checked and set aside" },
] as const;

function EvidenceTab({ notes, run }: { notes: EvidenceNote[]; run: RunDetail | null }) {
  if (notes.length === 0) return <p className="muted">No evidence has been collected yet.</p>;
  const stepFor = new Map(
    (run?.steps ?? [])
      .filter((s) => typeof s.metadata.evidence_id === "string")
      .map((s) => [String(s.metadata.evidence_id), s.id]),
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {GROUPS.map(({ role, title }) => {
        const items = byRole(notes, role);
        if (items.length === 0) return null;
        return (
          <section key={role} className="ev-group">
            <h3>
              {title} <span className="tiny muted">· {items.length}</span>
            </h3>
            {items.map((n) => {
              const e = n.evidence;
              const step = stepFor.get(n.id);
              return (
                <details key={n.id} id={n.id} className="ev-item">
                  <summary>
                    <span className={`ev-dot ev-dot-${role}`} aria-hidden="true" />
                    <span>{n.sentence}</span>
                    <span className="tiny faint mono">{shortId(n.id)}</span>
                  </summary>
                  <div className="ev-detail">
                    <dl>
                      <dt>ID</dt>
                      <dd className="mono">{n.id}</dd>
                      <dt>Source</dt>
                      <dd>{e?.source ?? "missing"}</dd>
                      <dt>Query</dt>
                      <dd className="mono">
                        {e ? `${e.template_id} ${JSON.stringify(e.parameters)}` : "—"}
                      </dd>
                      <dt>Time range</dt>
                      <dd>
                        {e
                          ? `${new Date(e.window_start).toLocaleTimeString()} – ${new Date(e.window_end).toLocaleTimeString()}`
                          : "—"}
                      </dd>
                      <dt>Collected by</dt>
                      <dd>{step ? <Link href={`?tab=investigation#${step}`}>step {shortId(step)} · jump to step</Link> : "—"}</dd>
                      <dt>Used as</dt>
                      <dd>{role === "supports" ? "support" : role === "against" ? "contradiction" : "context only"}</dd>
                      <dt>Note</dt>
                      <dd>{n.fromAgent ? "written by the agent" : "generated from the record"}</dd>
                    </dl>
                    {e ? (
                      <pre>
                        {e.summary}
                        {e.truncated ? "\n… (truncated)" : ""}
                      </pre>
                    ) : null}
                    {e?.source_url ? (
                      <a href={e.source_url} target="_blank" rel="noreferrer">
                        Open in Grafana ↗
                      </a>
                    ) : null}
                  </div>
                </details>
              );
            })}
          </section>
        );
      })}
    </div>
  );
}

const NEXT_STEPS: Record<string, [string, string][]> = {
  RECEIVED: [["Agent", "Investigate"], ["Policy", "Check any proposed action"], ["Human", "Approve if required"]],
  TRIAGED: [["Agent", "Investigate"], ["Policy", "Check any proposed action"]],
  INVESTIGATING: [["Agent", "Complete the investigation"], ["Policy", "Check any proposed action"], ["Human", "Approve if required"]],
  DIAGNOSED: [["Policy", "Check the proposed action"], ["Human", "Approve if required"]],
  AWAITING_APPROVAL: [["Executor", "Run the approved action"], ["Verifier", "Check recovery independently"]],
  EXECUTING: [["Verifier", "Check recovery independently"]],
  VERIFYING: [["Verifier", "Decide resolved or escalate"]],
};

function TimelineTab({ incident }: { incident: IncidentDetail }) {
  const rows = lifecycleRows(incident.timeline, incident.state, incident.needs_human === true);
  const future = NEXT_STEPS[incident.state] ?? [];
  const alerts = mergeAlerts(incident.alert_events);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      <section>
        <h3 className="serif" style={{ margin: "0 0 8px", fontSize: 17 }}>
          Lifecycle
        </h3>
        <ol className="lanes">
          {rows.map((r) => (
            <li key={`${r.reference}-${r.at}`} className={`lane ${r.waiting ? "lane-waiting" : ""}`}>
              <span className="tiny mono">{r.waiting ? "now" : new Date(r.at).toLocaleTimeString()}</span>
              <span className="actor">{r.actor}</span>
              <span className="what">
                {r.text}
              </span>
            </li>
          ))}
          {future.map(([actor, text]) => (
            <li key={`${actor}-${text}`} className="lane lane-future">
              <span className="tiny">next</span>
              <span className="actor">{actor}</span>
              <span className="what">{text}</span>
            </li>
          ))}
        </ol>
      </section>
      <section>
        <h3 className="serif" style={{ margin: "0 0 8px", fontSize: 17 }}>
          Raw alerts
        </h3>
        <div className="table-scroll">
          <table className="ctable">
            <thead>
              <tr>
                <th>First</th>
                <th>Last</th>
                <th>Status</th>
                <th>Rule</th>
                <th>Deliveries</th>
                <th>Observed</th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((a) => (
                <tr key={`${a.first}-${a.status}`}>
                  <td>{new Date(a.first).toLocaleTimeString()}</td>
                  <td>{new Date(a.last).toLocaleTimeString()}</td>
                  <td>
                    <span className={`pill ${a.status === "resolved" ? "pill-green" : "pill-red"}`}>{a.status}</span>
                  </td>
                  <td className="mono small">{a.alert_name}</td>
                  <td>{a.count}</td>
                  <td>{a.observed ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

export async function IncidentPane({
  incidentId,
  flash,
}: {
  incidentId: string;
  tab: TabKey;
  flash: PaneFlash;
}) {
  const authed = await sessionFetch();
  const [result, me] = await Promise.all([
    fetchIncident(apiBaseUrl(), incidentId, authed),
    fetchMe(apiBaseUrl(), authed),
  ]);
  if (!result.ok) {
    return (
      <main className="pane">
        <LoadError what="incident" error={result.error} next={`/incidents/${incidentId}`} />
      </main>
    );
  }
  const incident = result.data;
  const lastAction = incident.actions.at(-1);
  const run = diagnosisRun(incident.runs);
  const [actionResult, runResult] = await Promise.all([
    lastAction ? fetchAction(apiBaseUrl(), lastAction.id, authed) : Promise.resolve(null),
    run ? fetchRun(apiBaseUrl(), run.id, authed) : Promise.resolve(null),
  ]);
  const action = actionResult?.ok ? actionResult.data : null;
  const runDetail = runResult?.ok ? runResult.data : null;
  const notes = evidenceNotes(incident.diagnosis?.diagnosis ?? null, runDetail?.evidence ?? []);
  const status = inboxStatus(incident);
  const story = buildStory(incident, action);
  const canOperate = me !== null && OPERATE.has(me.role);
  return (
    <main className="pane">
      <AutoRefresh active={incidentIsLive(incident, incident.actions)} />
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <span className="pane-meta">
          {incident.service_id} · {incident.environment} · {incident.severity} · opened{" "}
          {new Date(incident.created_at).toLocaleTimeString()} · <span className={`pill pill-${status.tone === "violet" ? "violet" : status.tone === "red" ? "red" : status.tone === "amber" ? "amber" : status.tone === "green" ? "green" : "gray"}`}>{status.label}</span>
        </span>
        <h1 className="incident-headline">{story.headline}</h1>
        <p className="incident-summary">{story.summary}</p>
        {incident.diagnosis ? <IncidentTabLink incidentId={incident.id} tab="investigation" hash="#full-diagnosis" className="small">Read full diagnosis →</IncidentTabLink> : null}
        <span className="tiny faint">
          {incidentTitle(incident)} · <span className="mono">{incident.id}</span> ·{" "}
          <Link href={`/reports?id=${incident.id}`}>Report</Link>
        </span>
      </div>
      {flash.error ? <p className="notice notice-error">Could not do that: {flash.error.replace(/_/g, " ")}</p> : null}
      {flash.done === "assigned" ? <p className="notice notice-ok">You now own this incident.</p> : null}
      {flash.done === "closed" ? <p className="notice notice-ok">Closed. Your decision is in the audit log.</p> : null}
      {incident.needs_human ? (
        <Handover incident={incident} username={me?.username ?? null} canOperate={canOperate} flash={flash} />
      ) : incident.disposition ? (
        <p className="notice notice-info">
          Closed by {incident.disposed_by} as “{DISPOSITION_LABEL[incident.disposition]}”
          {incident.disposed_at ? ` · ${new Date(incident.disposed_at).toLocaleString()}` : ""}
          {incident.disposition_note ? ` · ${incident.disposition_note}` : ""}
        </p>
      ) : null}
      <IncidentTabs incidentId={incident.id} panels={{
        story: <StoryTab incident={incident} action={action} notes={notes} />,
        investigation: <InvestigationTab run={runDetail} notes={notes} incident={incident} />,
        evidence: <EvidenceTab notes={notes} run={runDetail} />,
        timeline: <TimelineTab incident={incident} />,
      }} />
      <ChatDock incidentId={incident.id} title={incidentTitle(incident)} />
    </main>
  );
}
