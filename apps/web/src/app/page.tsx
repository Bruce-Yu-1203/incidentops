import { connection } from "next/server";
import { Suspense } from "react";

import { apiBaseUrl, fetchHealth } from "@/lib/api";

// Reads live control-plane state, so it must render at request time.
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
        <p>The incident list arrives with alert ingestion (roadmap P1).</p>
      </section>
    </main>
  );
}
