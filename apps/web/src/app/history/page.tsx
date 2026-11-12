import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { apiBaseUrl } from "@/lib/api";
import {
  apiParams,
  fetchHistory,
  formatSeconds,
  type HistoryOutcome,
  OUTCOME_LABEL,
  OUTCOMES,
  pageHref,
  parseHistoryQuery,
  RANGES,
} from "@/lib/history";
import { incidentTitle } from "@/lib/inbox";
import { sessionFetch } from "@/lib/session";
import { LoadError } from "@/app/load-error";
import { rootCauseLabel, rootCauseOptions } from "@/lib/vocabulary";

type Search = Promise<Record<string, string | string[] | undefined>>;
const PAGE_SIZE = 25;

const OUTCOME_PILL: Record<HistoryOutcome, string> = {
  auto_resolved: "pill-green",
  closed_by_person: "pill-gray",
  needs_person: "pill-red",
  cancelled: "pill-gray",
  open: "pill-violet",
};

async function HistoryView({ searchParams }: { searchParams: Search }) {
  await connection();
  const query = parseHistoryQuery(await searchParams);
  const result = await fetchHistory(apiBaseUrl(), apiParams(query, new Date(), PAGE_SIZE), await sessionFetch());
  if (!result.ok) return <LoadError what="history" error={result.error} next="/history" />;
  const page = result.data;
  const pages = Math.max(1, Math.ceil(page.total / PAGE_SIZE));
  const exportHref = pageHref({ ...query, page: 1 }, {}, "/history/export");
  return (
    <>
      <form className="filters" action="/history">
        <label>
          Search
          <input type="search" name="q" defaultValue={query.q ?? ""} placeholder="Incident, service, cause or ID" />
        </label>
        <label>
          Time range
          <select name="range" defaultValue={query.range}>
            {Object.keys(RANGES).map((r) => (
              <option key={r} value={r}>
                Last {r}
              </option>
            ))}
          </select>
        </label>
        <label>
          Outcome
          <select name="outcome" defaultValue={query.outcome ?? ""}>
            <option value="">Any</option>
            {OUTCOMES.map((o) => (
              <option key={o} value={o}>
                {OUTCOME_LABEL[o]}
              </option>
            ))}
          </select>
        </label>
        <label>
          Service
          <select name="service_id" defaultValue={query.service_id ?? ""}>
            <option value="">Any</option>
            {page.services.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        <label>
          Root cause
          <select name="root_cause_id" defaultValue={query.root_cause_id ?? ""}>
            <option value="">Any</option>
            {rootCauseOptions(page.root_causes).map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Confidence
          <select name="confidence" defaultValue={query.confidence ?? ""}>
            <option value="">Any</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>
        <button type="submit" className="btn btn-dark">
          Apply
        </button>
        <Link href="/history" className="small">
          Reset
        </Link>
      </form>

      <div className="stats">
        <div className="stat-card">
          <span className="label">Incidents</span>
          <span className="big">{page.stats.count}</span>
        </div>
        <div className="stat-card">
          <span className="label">Auto-resolved</span>
          <span className="big">{page.stats.auto_resolved_pct === null ? "—" : `${page.stats.auto_resolved_pct}%`}</span>
          <span className="tiny muted">{page.stats.auto_resolved} verified by the system</span>
        </div>
        <div className="stat-card">
          <span className="label">Median time to diagnosis</span>
          <span className="big">{formatSeconds(page.stats.median_time_to_diagnosis_s)}</span>
        </div>
        <div className="stat-card">
          <span className="label">Median time to recovery</span>
          <span className="big">{formatSeconds(page.stats.median_time_to_recovery_s)}</span>
        </div>
      </div>

      <div className="row between">
        <span className="small muted">
          {page.total} matching · page {page.page} of {pages}
        </span>
        <a href={exportHref} className="btn">
          Export CSV
        </a>
      </div>
      <div className="table-scroll">
        <table className="ctable">
          <thead>
            <tr>
              <th>Opened</th>
              <th>Incident</th>
              <th>Outcome</th>
              <th>Diagnosis</th>
              <th>To diagnose</th>
              <th>To recover</th>
              <th>Approved by</th>
            </tr>
          </thead>
          <tbody>
            {page.rows.length === 0 ? (
              <tr>
                <td colSpan={7} className="muted">
                  No incidents match these filters.
                </td>
              </tr>
            ) : null}
            {page.rows.map((r) => (
              <tr key={r.id}>
                <td className="small">{new Date(r.opened_at).toLocaleString()}</td>
                <td>
                  <Link href={`/incidents/${r.id}`}>
                    <strong>{incidentTitle({ title: r.title, dedup_key: r.id })}</strong>
                  </Link>
                  <br />
                  <span className="tiny muted">{r.service_id}</span>
                </td>
                <td>
                  <span className={`pill ${OUTCOME_PILL[r.outcome]}`}>{OUTCOME_LABEL[r.outcome]}</span>
                </td>
                <td className="small">
                  {r.root_cause_id ? rootCauseLabel(r.root_cause_id) : "—"}
                  {r.confidence ? <span className="muted"> · {r.confidence}</span> : null}
                </td>
                <td className="small">{formatSeconds(r.time_to_diagnosis_s)}</td>
                <td className="small">{formatSeconds(r.time_to_recovery_s)}</td>
                <td className="small">{r.approved_by ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <nav className="pager" aria-label="Pages">
        {page.page > 1 ? <Link href={pageHref(query, { page: page.page - 1 })}>← Newer</Link> : null}
        {page.page < pages ? <Link href={pageHref(query, { page: page.page + 1 })}>Older →</Link> : null}
      </nav>
    </>
  );
}

export default function HistoryPage({ searchParams }: { searchParams: Search }) {
  return (
    <main className="page">
      <div>
        <h1>History</h1>
        <p className="muted" style={{ margin: "4px 0 0" }}>
          Every incident, open or closed. Auto-resolved means the system verified recovery itself.
        </p>
      </div>
      <Suspense fallback={<p>Loading history…</p>}>
        <HistoryView searchParams={searchParams} />
      </Suspense>
    </main>
  );
}
