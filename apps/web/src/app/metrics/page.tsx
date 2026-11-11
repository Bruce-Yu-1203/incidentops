import { connection } from "next/server";
import { Suspense } from "react";

import { MetricsDashboard } from "@/components/metrics-dashboard";
import { apiBaseUrl } from "@/lib/api";
import { fetchMetrics, parseRange } from "@/lib/metrics";
import { sessionFetch } from "@/lib/session";
import { LoadError } from "@/app/load-error";

type Search = Promise<Record<string, string | string[] | undefined>>;

async function MetricsView({ searchParams }: { searchParams: Search }) {
  await connection();
  const range = parseRange((await searchParams).range);
  const result = await fetchMetrics(apiBaseUrl(), range, await sessionFetch());
  if (!result.ok) return <LoadError what="metrics" error={result.error} next="/metrics" />;
  return <MetricsDashboard initial={result.data} initialRange={range} />;
}

export default function MetricsPage({ searchParams }: { searchParams: Search }) {
  return (
    <main className="page metrics-page">
      <div className="metrics-page-heading"><span className="eyebrow">Observability</span><h1>Metrics</h1><p>Live service signals, with the context to understand what changed.</p></div>
      <Suspense fallback={<div className="metric-empty" role="status">Loading service signals…</div>}>
        <MetricsView searchParams={searchParams} />
      </Suspense>
    </main>
  );
}
