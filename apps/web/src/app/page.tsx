import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { AutoRefresh } from "@/components/auto-refresh";
import { ProgressBar } from "@/components/progress-bar";
import { StateBadge, ToneIcon } from "@/components/state-badge";
import { apiBaseUrl, fetchHealth } from "@/lib/api";
import {
  type AttentionBucket,
  filterIncidents,
  formatAge,
  groupAttention,
  type ListView,
  parseView,
} from "@/lib/attention";
import { fetchIncidents, type IncidentSummary } from "@/lib/incidents";
import { incidentLifecycle } from "@/lib/lifecycle";
import { listIsLive } from "@/lib/refresh";
import { sessionFetch } from "@/lib/session";
import type { Tone } from "@/lib/states";

// Both panels read live control-plane state, so they render at request time.
async function ApiStatus() {
  await connection();
  const health = await fetchHealth(apiBaseUrl());
  return health.ok ? (
    <p data-testid="api-status" className="subtitle">
      API healthy · version {health.data.version} · profile {health.data.profile}
    </p>
  ) : (
    <p data-testid="api-status" className="error">
      API unavailable: {health.error}
    </p>
  );
}

const BUCKETS: { bucket: AttentionBucket; title: string; hint: string; tone: Tone }[] = [
  {
    bucket: "awaiting_approval",
    title: "Awaiting approval",
    hint: "An approver must decide",
    tone: "attention",
  },
  {
    bucket: "escalated",
    title: "Escalated",
    hint: "Automation stopped; a human owns it",
    tone: "danger",
  },
  {
    bucket: "in_progress",
    title: "In progress",
    hint: "Investigating, executing or verifying",
    tone: "progress",
  },
  {
    bucket: "failed",
    title: "Platform failure",
    hint: "IncidentOps itself failed",
    tone: "danger",
  },
];

function NeedsAttention({ incidents, now }: { incidents: IncidentSummary[]; now: Date }) {
  const groups = groupAttention(incidents);
  const urgent = [...groups.awaiting_approval, ...groups.escalated];
  return (
    <section aria-labelledby="attention-heading" className="stack">
      <h2 id="attention-heading" className="eyebrow">
        Needs attention
      </h2>
      <div className="stat-grid">
        {BUCKETS.map(({ bucket, title, hint, tone }) => (
          <div key={bucket} className={`stat tone-${tone}`} data-bucket={bucket}>
            <span className="stat-title">
              <ToneIcon tone={tone} size={14} />
              {title}
            </span>
            <span className="stat-value">{groups[bucket].length}</span>
            <span className="subtitle stat-hint">{hint}</span>
          </div>
        ))}
      </div>
      {urgent.length > 0 ? (
        <ul className="attention-list">
          {urgent.map((incident) => (
            <li key={incident.id}>
              <StateBadge machine="incident" state={incident.state} />
              <Link href={`/incidents/${incident.id}`} className="attention-title">
                {incident.dedup_key}
              </Link>
              <span className="subtitle">
                {incident.service_id} · opened {formatAge(incident.created_at, now)} ago
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

const VIEWS: { view: ListView; label: string }[] = [
  { view: "open", label: "Open" },
  { view: "closed", label: "Closed" },
  { view: "all", label: "All" },
];

async function IncidentList({ searchParams }: { searchParams: Promise<{ view?: string }> }) {
  await connection();
  const [result, params] = await Promise.all([
    fetchIncidents(apiBaseUrl(), await sessionFetch()),
    searchParams,
  ]);
  if (!result.ok) {
    return <p className="error">Could not load incidents: {result.error}</p>;
  }
  const now = new Date();
  const view = parseView(params.view);
  const shown = filterIncidents(result.data, view);
  return (
    <>
      <AutoRefresh active={listIsLive(result.data)} />
      <NeedsAttention incidents={result.data} now={now} />
      <section aria-labelledby="incidents-heading" className="stack">
        <div className="row-between">
          <h2 id="incidents-heading" className="eyebrow">
            Incidents
          </h2>
          <nav className="pills" aria-label="Filter incidents">
            {VIEWS.map(({ view: v, label }) => (
              <Link
                key={v}
                href={v === "open" ? "/" : `/?view=${v}`}
                className={v === view ? "pill pill-active" : "pill"}
                aria-current={v === view ? "page" : undefined}
              >
                {label} · {filterIncidents(result.data, v).length}
              </Link>
            ))}
          </nav>
        </div>
        {shown.length === 0 ? (
          <p>No incidents in this view.</p>
        ) : (
          <div className="table-box">
            <table>
              <thead>
                <tr>
                  <th>State</th>
                  <th>Progress</th>
                  <th>Rule key</th>
                  <th>Service</th>
                  <th>Severity</th>
                  <th>Age</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((incident) => (
                  <tr key={incident.id}>
                    <td>
                      <StateBadge machine="incident" state={incident.state} />
                    </td>
                    <td>
                      <ProgressBar lifecycle={incidentLifecycle(incident.state)} />
                    </td>
                    <td>
                      <Link href={`/incidents/${incident.id}`}>{incident.dedup_key}</Link>
                    </td>
                    <td>{incident.service_id}</td>
                    <td>{incident.severity}</td>
                    <td>
                      <time
                        dateTime={incident.created_at}
                        title={new Date(incident.created_at).toLocaleString()}
                      >
                        {formatAge(incident.created_at, now)}
                      </time>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

export default function Home({ searchParams }: { searchParams: Promise<{ view?: string }> }) {
  return (
    <main className="container wide stack-lg">
      <div>
        <h1>IncidentOps</h1>
        <p className="subtitle">Policy-governed incident investigation — lab prototype</p>
        <Suspense fallback={<p className="subtitle">Checking API…</p>}>
          <ApiStatus />
        </Suspense>
      </div>
      <Suspense fallback={<p>Loading incidents…</p>}>
        <IncidentList searchParams={searchParams} />
      </Suspense>
    </main>
  );
}
