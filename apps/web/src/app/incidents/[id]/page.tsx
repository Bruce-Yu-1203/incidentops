import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { AutoRefresh } from "@/components/auto-refresh";
import { StateBadge } from "@/components/state-badge";
import { Stepper } from "@/components/stepper";
import { Timeline } from "@/components/timeline";
import { apiBaseUrl } from "@/lib/api";
import { formatAge } from "@/lib/attention";
import { CONFIDENCE_LEVELS, diagnosisRun, evidenceHref, shortId } from "@/lib/evidence";
import { fetchIncident, type IncidentDetail } from "@/lib/incidents";
import { incidentLifecycle, visitedStates } from "@/lib/lifecycle";
import { incidentIsLive } from "@/lib/refresh";
import { sessionFetch } from "@/lib/session";
import { buildTimeline } from "@/lib/timeline";

function EvidenceChips({
  ids,
  runId,
  kind,
}: {
  ids: string[];
  runId: string | null;
  kind: "supporting" | "contradicting";
}) {
  return (
    <div className="chips">
      {ids.map((id) => {
        const href = evidenceHref(runId, id);
        const text = <code>{shortId(id)}</code>;
        return href ? (
          <Link key={id} href={href} className={`chip chip-${kind}`} title={id}>
            {text}
          </Link>
        ) : (
          <span key={id} className={`chip chip-${kind}`} title={id}>
            {text}
          </span>
        );
      })}
    </div>
  );
}

function DiagnosisPanel({ incident }: { incident: IncidentDetail }) {
  const report = incident.diagnosis;
  if (!report) {
    return (
      <section className="panel stack">
        <h2>Diagnosis</h2>
        <p className="subtitle">No diagnosis yet.</p>
      </section>
    );
  }
  const d = report.diagnosis;
  const run = diagnosisRun(incident.runs);
  const supporting = d.supporting_evidence_ids ?? [];
  const contradicting = d.contradicting_evidence_ids ?? [];
  const questions = d.unresolved_questions ?? [];
  return (
    <section className="panel stack" aria-labelledby="diagnosis-heading">
      <div className="row-between">
        <h2 id="diagnosis-heading">
          Diagnosis{" "}
          <span className="subtitle heading-note">· a hypothesis, not a confirmed cause</span>
        </h2>
        <span className="confidence" aria-label={`Confidence ${d.confidence_level} (categorical)`}>
          <span className="subtitle">Confidence</span>
          {CONFIDENCE_LEVELS.map((level) => (
            <span
              key={level}
              className={level === d.confidence_level.toLowerCase() ? "level level-on" : "level"}
            >
              {level}
            </span>
          ))}
        </span>
      </div>
      <p className="lead">
        <strong>{d.root_cause_id}</strong> on <code>{d.affected_component}</code>
      </p>
      <p>{d.summary}</p>
      <div className="stack-sm">
        <span className="eyebrow tone-success-text">Supporting · {supporting.length}</span>
        {supporting.length > 0 ? (
          <EvidenceChips ids={supporting} runId={run?.id ?? null} kind="supporting" />
        ) : (
          <span className="subtitle">None cited.</span>
        )}
      </div>
      <div className="stack-sm">
        <span className="eyebrow tone-unknown-text">Contradicting · {contradicting.length}</span>
        {contradicting.length > 0 ? (
          <EvidenceChips ids={contradicting} runId={run?.id ?? null} kind="contradicting" />
        ) : (
          <span className="subtitle">None found.</span>
        )}
      </div>
      {questions.length > 0 ? (
        <div className="stack-sm divider">
          <span className="eyebrow">Unresolved questions</span>
          <ul className="tight">
            {questions.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {report.needs_human_review ? (
        <p className="error">The agent asked for human review.</p>
      ) : null}
      {run ? (
        <Link href={`/investigations/${run.id}`}>
          View investigation trajectory ({run.step_count} steps, {run.tool_call_count} tool calls) →
        </Link>
      ) : null}
    </section>
  );
}

function ActionsPanel({ incident }: { incident: IncidentDetail }) {
  const proposed = incident.diagnosis?.proposed_action ?? null;
  const pending = incident.actions.find((a) => a.state === "PENDING_APPROVAL");
  return (
    <section
      className={pending ? "panel stack panel-attention" : "panel stack"}
      aria-labelledby="actions-heading"
    >
      <h2 id="actions-heading">Actions</h2>
      {incident.actions.length === 0 ? (
        proposed ? (
          <p>
            Proposed (not approved):{" "}
            <code>
              {proposed.action_type} → {proposed.target_id}
            </code>{" "}
            — awaiting deterministic policy evaluation.
          </p>
        ) : (
          <p className="subtitle">No action proposed.</p>
        )
      ) : (
        <ul className="rows">
          {incident.actions.map((action) => (
            <li key={action.id}>
              <StateBadge machine="action" state={action.state} />
              <Link href={`/actions/${action.id}`} className="mono">
                {action.action_type} → {action.target_id}
              </Link>
              {action.state === "PENDING_APPROVAL" ? (
                <span className="subtitle">
                  expires{" "}
                  <time dateTime={action.expires_at}>
                    {new Date(action.expires_at).toLocaleTimeString()}
                  </time>
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      {pending ? (
        <Link href={`/actions/${pending.id}`} className="button button-attention">
          Review exact action
        </Link>
      ) : null}
    </section>
  );
}

async function IncidentView({ params }: { params: Promise<{ id: string }> }) {
  await connection();
  const { id } = await params;
  const result = await fetchIncident(apiBaseUrl(), id, await sessionFetch());
  if (!result.ok) {
    return <p className="error">Could not load incident: {result.error}</p>;
  }
  const incident = result.data;
  const now = new Date();
  return (
    <>
      <AutoRefresh active={incidentIsLive(incident, incident.actions)} />
      <section className="panel stack" aria-labelledby="incident-title">
        <div className="row-between">
          <div className="stack-sm">
            <h1 id="incident-title">{incident.dedup_key}</h1>
            <span className="subtitle">
              {incident.service_id} · {incident.environment} · {incident.severity} · opened{" "}
              {formatAge(incident.created_at, now)} ago · <code>{incident.id}</code> · v
              {incident.version}
            </span>
          </div>
          <StateBadge machine="incident" state={incident.state} />
        </div>
        <Stepper
          label="Incident lifecycle"
          lifecycle={incidentLifecycle(
            incident.state,
            visitedStates(
              incident.timeline.filter((e) => e.kind === "audit").map((e) => e.summary),
            ),
          )}
        />
        {incident.escalation_reason ? (
          <p className="error">Escalation reason: {incident.escalation_reason}</p>
        ) : null}
      </section>

      <div className="columns">
        <div className="col-main stack-lg">
          <DiagnosisPanel incident={incident} />
          <ActionsPanel incident={incident} />
          <section className="panel stack" aria-labelledby="runs-heading">
            <h2 id="runs-heading">Investigations</h2>
            {incident.runs.length === 0 ? (
              <p className="subtitle">No investigation runs yet.</p>
            ) : (
              <ul className="rows">
                {incident.runs.map((run) => (
                  <li key={run.id}>
                    <StateBadge machine="run" state={run.state} />
                    <Link href={`/investigations/${run.id}`}>
                      <code>{shortId(run.id)}</code>
                    </Link>
                    <span className="subtitle">
                      {run.tool_call_count} tool calls{run.error_code ? ` · ${run.error_code}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="panel stack" aria-labelledby="alerts-heading">
            <h2 id="alerts-heading">Observed alerts</h2>
            <div className="table-box flat">
              <table>
                <thead>
                  <tr>
                    <th>Received</th>
                    <th>Status</th>
                    <th>Rule</th>
                    <th>Summary</th>
                    <th>Observed</th>
                  </tr>
                </thead>
                <tbody>
                  {incident.alert_events.map((event) => (
                    <tr key={event.id}>
                      <td>{new Date(event.received_at).toLocaleString()}</td>
                      <td>{event.status}</td>
                      <td>{event.alert_name}</td>
                      <td>{event.annotations.summary ?? "—"}</td>
                      <td>{event.annotations.observed_value ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>

        <aside className="col-side panel stack" aria-labelledby="timeline-heading">
          <h2 id="timeline-heading">Timeline</h2>
          <Timeline items={buildTimeline(incident.timeline)} />
        </aside>
      </div>
    </>
  );
}

export default function IncidentPage({ params }: { params: Promise<{ id: string }> }) {
  return (
    <main className="container wide stack-lg">
      <p className="crumb">
        <Link href="/">← All incidents</Link>
      </p>
      <Suspense fallback={<p>Loading incident…</p>}>
        <IncidentView params={params} />
      </Suspense>
    </main>
  );
}
