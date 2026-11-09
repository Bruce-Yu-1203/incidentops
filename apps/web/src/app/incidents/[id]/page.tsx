import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { apiBaseUrl } from "@/lib/api";
import { fetchIncident } from "@/lib/incidents";

async function IncidentView({ params }: { params: Promise<{ id: string }> }) {
  await connection();
  const { id } = await params;
  const result = await fetchIncident(apiBaseUrl(), id);
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
