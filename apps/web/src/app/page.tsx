import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { apiBaseUrl, fetchHealth } from "@/lib/api";
import { fetchIncidents } from "@/lib/incidents";

// Both panels read live control-plane state, so they render at request time.
async function ApiStatus() {
  await connection();
  const health = await fetchHealth(apiBaseUrl());
  return health.ok ? (
    <p data-testid="api-status">
      API healthy · version {health.data.version} · profile {health.data.profile}
    </p>
  ) : (
    <p data-testid="api-status" className="error">
      API unavailable: {health.error}
    </p>
  );
}

async function IncidentList() {
  await connection();
  const result = await fetchIncidents(apiBaseUrl());
  if (!result.ok) {
    return <p className="error">Could not load incidents: {result.error}</p>;
  }
  if (result.data.length === 0) {
    return <p>No incidents yet.</p>;
  }
  return (
    <table>
      <thead>
        <tr>
          <th>Created</th>
          <th>Service</th>
          <th>Rule key</th>
          <th>Severity</th>
          <th>State</th>
        </tr>
      </thead>
      <tbody>
        {result.data.map((incident) => (
          <tr key={incident.id}>
            <td>{new Date(incident.created_at).toLocaleString()}</td>
            <td>{incident.service_id}</td>
            <td>
              <Link href={`/incidents/${incident.id}`}>{incident.dedup_key}</Link>
            </td>
            <td>{incident.severity}</td>
            <td>
              <span className="badge">{incident.state}</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Home() {
  return (
    <main className="container">
      <h1>IncidentOps</h1>
      <p className="subtitle">Policy-governed incident investigation — lab prototype</p>
      <section aria-labelledby="status-heading">
        <h2 id="status-heading">Control plane</h2>
        <Suspense fallback={<p>Checking API…</p>}>
          <ApiStatus />
        </Suspense>
      </section>
      <section aria-labelledby="incidents-heading">
        <h2 id="incidents-heading">Incidents</h2>
        <Suspense fallback={<p>Loading incidents…</p>}>
          <IncidentList />
        </Suspense>
      </section>
    </main>
  );
}
