import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { apiBaseUrl } from "@/lib/api";
import { sessionFetch } from "@/lib/session";
import { fetchIncident } from "@/lib/incidents";

async function IncidentView({ params }: { params: Promise<{ id: string }> }) {
  await connection();
  const { id } = await params;
  const result = await fetchIncident(apiBaseUrl(), id, await sessionFetch());
  if (!result.ok) {
    return <p className="error">Could not load incident: {result.error}</p>;
  }
  const incident = result.data;
  return (
    <>
      <h1>{incident.dedup_key}</h1>
      <dl className="facts">
        <dt>State</dt>
        <dd>
          <span className="badge">{incident.state}</span> (version {incident.version})
        </dd>
        <dt>Service</dt>
        <dd>
          {incident.service_id} · {incident.environment}
        </dd>
        <dt>Severity</dt>
        <dd>{incident.severity}</dd>
        <dt>Incident ID</dt>
        <dd>
          <code>{incident.id}</code>
        </dd>
      </dl>

      <h2>Diagnosis</h2>
      {incident.diagnosis ? (
        <div className="panel">
          <p>
            <strong>{incident.diagnosis.diagnosis.root_cause_id}</strong> on{" "}
            {incident.diagnosis.diagnosis.affected_component} · confidence{" "}
            {incident.diagnosis.diagnosis.confidence_level} (categorical, not a probability)
          </p>
          <p>{incident.diagnosis.diagnosis.summary}</p>
          <p className="subtitle">
            Supporting evidence: {incident.diagnosis.diagnosis.supporting_evidence_ids.length} ·
            contradicting: {incident.diagnosis.diagnosis.contradicting_evidence_ids.length}
          </p>
          {incident.diagnosis.proposed_action ? (
            <p>
              Proposed (not approved) action:{" "}
              <code>
                {incident.diagnosis.proposed_action.action_type} →{" "}
                {incident.diagnosis.proposed_action.target_id}
              </code>{" "}
              — awaiting deterministic policy evaluation.
            </p>
          ) : (
            <p>No action proposed{incident.diagnosis.needs_human_review ? "; human review requested" : ""}.</p>
          )}
        </div>
      ) : (
        <p>No diagnosis yet.</p>
      )}
      {incident.escalation_reason ? (
        <p className="error">Escalation reason: {incident.escalation_reason}</p>
      ) : null}

      <h2>Actions</h2>
      {incident.actions.length === 0 ? (
        <p>No actions.</p>
      ) : (
        <ul>
          {incident.actions.map((action) => (
            <li key={action.id}>
              <Link href={`/actions/${action.id}`}>
                {action.action_type} → {action.target_id}
              </Link>{" "}
              · <span className="badge">{action.state}</span>
            </li>
          ))}
        </ul>
      )}

      <h2>Investigations</h2>
      {incident.runs.length === 0 ? (
        <p>No investigation runs yet.</p>
      ) : (
        <ul>
          {incident.runs.map((run) => (
            <li key={run.id}>
              <Link href={`/investigations/${run.id}`}>{run.id}</Link> ·{" "}
              <span className="badge">{run.state}</span> · {run.tool_call_count} tool calls
              {run.error_code ? ` · ${run.error_code}` : ""}
            </li>
          ))}
        </ul>
      )}

      <h2>Observed alerts</h2>
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

      <h2>Timeline</h2>
      <ol className="timeline">
        {incident.timeline.map((entry) => (
          <li key={`${entry.kind}-${entry.reference_id}`}>
            <time dateTime={entry.at}>{new Date(entry.at).toLocaleString()}</time> ·{" "}
            <strong>{entry.kind}</strong> · {entry.summary}
          </li>
        ))}
      </ol>
    </>
  );
}

export default function IncidentPage({ params }: { params: Promise<{ id: string }> }) {
  return (
    <main className="container">
      <p>
        <Link href="/">← All incidents</Link>
      </p>
      <Suspense fallback={<p>Loading incident…</p>}>
        <IncidentView params={params} />
      </Suspense>
    </main>
  );
}
