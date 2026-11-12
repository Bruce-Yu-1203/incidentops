import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { CopyButton } from "@/components/copy-button";
import { PrintButton } from "@/components/print-button";
import { type ActionDetail, fetchAction } from "@/lib/actions";
import { apiBaseUrl } from "@/lib/api";
import { diagnosisRun } from "@/lib/evidence";
import { byRole, evidenceNotes } from "@/lib/evidence-notes";
import { fetchHistory, formatSeconds } from "@/lib/history";
import { incidentTitle } from "@/lib/inbox";
import { DISPOSITION_LABEL, fetchIncident, isIncidentId } from "@/lib/incidents";
import { fetchRun } from "@/lib/investigations";
import { buildReport, reportDurations, reportMarkdown } from "@/lib/report";
import { sessionFetch } from "@/lib/session";
import { LoadError } from "@/app/load-error";
import { rootCauseLabel } from "@/lib/vocabulary";

type Search = Promise<Record<string, string | string[] | undefined>>;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);
const secs = (ms: number | null) => formatSeconds(ms === null ? null : ms / 1000);

async function ReportDoc({ id }: { id: string }) {
  const authed = await sessionFetch();
  const result = await fetchIncident(apiBaseUrl(), id, authed);
  if (!result.ok) return <LoadError what="report" error={result.error} next={`/reports?id=${id}`} />;
  const incident = result.data;
  const run = diagnosisRun(incident.runs);
  const [loaded, runResult] = await Promise.all([
    Promise.all(incident.actions.map((a) => fetchAction(apiBaseUrl(), a.id, authed))),
    run ? fetchRun(apiBaseUrl(), run.id, authed) : Promise.resolve(null),
  ]);
  const actions = loaded.filter((r): r is { ok: true; data: ActionDetail } => r.ok).map((r) => r.data);
  const report = buildReport(incident, actions);
  const durations = reportDurations(incident, report);
  const d = incident.diagnosis?.diagnosis ?? null;
  const notes = evidenceNotes(d, runResult?.ok ? runResult.data.evidence : []);
  const title = incidentTitle(incident);
  const markdown = reportMarkdown(incident, report, title, notes);
  return (
    <div className="report-doc">
      <div className="row between">
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="tiny muted">Incident report{report.final ? "" : " · provisional"}</span>
          <h1 style={{ fontSize: 28 }}>{title}</h1>
          <span className="small muted">
            {incident.service_id} · {incident.environment} · {incident.severity} · <span className="mono">{incident.id}</span>
          </span>
        </div>
        <span className="row no-print">
          <CopyButton value={markdown} label="Copy report as Markdown" text="Copy as Markdown" className="btn" />
          <PrintButton className="btn" />
        </span>
      </div>
      <div className={`banner banner-${report.tone}`}>{report.headline}</div>
      {incident.disposition ? (
        <p className="small" style={{ margin: 0 }}>
          Closed by {incident.disposed_by}: “{DISPOSITION_LABEL[incident.disposition]}”
          {incident.disposition_note ? ` — ${incident.disposition_note}` : ""}
        </p>
      ) : null}
      <div className="stats">
        <div className="stat-card" style={{ background: "var(--background)" }}>
          <span className="label">To diagnosis</span>
          <span className="big">{secs(durations.toDiagnosis)}</span>
        </div>
        <div className="stat-card" style={{ background: "var(--background)" }}>
          <span className="label">To approval</span>
          <span className="big">{secs(durations.toApproval)}</span>
        </div>
        <div className="stat-card" style={{ background: "var(--background)" }}>
          <span className="label">To verified recovery</span>
          <span className="big">{secs(durations.toRecovery)}</span>
        </div>
        <div className="stat-card" style={{ background: "var(--background)" }}>
          <span className="label">Closed after</span>
          <span className="big">{report.final ? secs(durations.open) : "still open"}</span>
        </div>
      </div>
      <section>
        <h2>Milestones</h2>
        <table className="ctable">
          <tbody>
            {report.milestones.map((m) => (
              <tr key={m.label}>
                <td>{m.label}</td>
                <td className="small">{m.at ? new Date(m.at).toLocaleString() : "—"}</td>
                <td className="small muted">{m.sinceOpen ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section>
        <h2>What we think happened</h2>
        {d ? (
          <>
            <p>
              {d.summary} We read this as <strong>{rootCauseLabel(d.root_cause_id)}</strong> on{" "}
              <code>{d.affected_component}</code>, with {d.confidence_level} confidence. It is the agent&apos;s hypothesis;
              only a passed independent verification confirms recovery.
            </p>
            {byRole(notes, "supports").length || byRole(notes, "against").length ? (
              <ul className="plain-list">
                {byRole(notes, "supports").map((n) => (
                  <li key={n.id}>✓ {n.sentence}</li>
                ))}
                {byRole(notes, "against").map((n) => (
                  <li key={n.id}>! {n.sentence}</li>
                ))}
              </ul>
            ) : null}
          </>
        ) : (
          <p className="muted">No diagnosis was produced.</p>
        )}
      </section>
      <section>
        <h2>Action</h2>
        {report.actions.length === 0 ? <p className="muted">No action was proposed.</p> : null}
        {report.actions.map((a) => {
          const detail = actions.find((x) => x.id === a.id);
          return (
            <table key={a.id} className="ctable" style={{ marginBottom: 10 }}>
              <tbody>
                <tr>
                  <td className="muted">Action</td>
                  <td>
                    <Link href={`/actions/${a.id}`} className="mono">
                      {a.title}
                    </Link>
                  </td>
                </tr>
                <tr>
                  <td className="muted">Policy</td>
                  <td className="small">{detail?.policy_version ?? "—"}</td>
                </tr>
                <tr>
                  <td className="muted">Decision</td>
                  <td className="small">{a.decision ? `${a.decision} by ${a.approver}` : "No decision recorded"}</td>
                </tr>
                <tr>
                  <td className="muted">Hash</td>
                  <td className="mono tiny" style={{ overflowWrap: "anywhere" }}>
                    {a.hash}
                  </td>
                </tr>
                <tr>
                  <td className="muted">Outcome</td>
                  <td className="small">{a.state.toLowerCase().replace(/_/g, " ")}</td>
                </tr>
                <tr>
                  <td className="muted">Verification</td>
                  <td className="small">
                    {a.verification ? a.verification.toLowerCase().replace(/_/g, " ") : "not run"}
                    {a.failedChecks.length ? ` — ${a.failedChecks.join("; ")}` : ""}
                  </td>
                </tr>
              </tbody>
            </table>
          );
        })}
      </section>
      {(d?.unresolved_questions ?? []).length > 0 ? (
        <section>
          <h2>Follow-ups</h2>
          <ul className="plain-list">
            {d!.unresolved_questions.map((q) => (
              <li key={q}>☐ {q}</li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

async function ReportsView({ searchParams }: { searchParams: Search }) {
  await connection();
  const params = await searchParams;
  const now = new Date();
  const query = new URLSearchParams({
    since: new Date(now.getTime() - 30 * 86_400_000).toISOString(),
    page_size: "50",
  });
  const list = await fetchHistory(apiBaseUrl(), query, await sessionFetch());
  if (!list.ok) return <LoadError what="reports" error={list.error} next="/reports" />;
  const requested = one(params.id);
  const selected = requested && isIncidentId(requested) ? requested : (list.data.rows[0]?.id ?? null);
  return (
    <div className="split">
      <aside aria-label="Reports" className="no-print">
        {list.data.rows.length === 0 ? <p className="muted small">No incidents in the last 30 days.</p> : null}
        {list.data.rows.map((r) => (
          <Link
            key={r.id}
            href={`/reports?id=${r.id}`}
            className={`inbox-card ${r.outcome === "auto_resolved" ? "accent-green" : r.outcome === "needs_person" ? "accent-red" : r.outcome === "open" ? "accent-violet" : "accent-gray"}`}
            aria-current={r.id === selected ? "true" : undefined}
          >
            <span className="meta">
              <span className="status">{r.outcome === "open" || r.outcome === "needs_person" ? "Provisional" : "Final"}</span>
              <span className="age">{new Date(r.opened_at).toLocaleDateString()}</span>
            </span>
            <strong>{incidentTitle({ title: r.title, dedup_key: r.id })}</strong>
            <span className="sub">{r.root_cause_id ? rootCauseLabel(r.root_cause_id) : r.service_id}</span>
          </Link>
        ))}
      </aside>
      <article>{selected ? <ReportDoc id={selected} /> : <p className="muted">Pick an incident.</p>}</article>
    </div>
  );
}

export default function ReportsPage({ searchParams }: { searchParams: Search }) {
  return (
    <main className="page">
      <h1 className="no-print">Reports</h1>
      <Suspense fallback={<p>Loading reports…</p>}>
        <ReportsView searchParams={searchParams} />
      </Suspense>
    </main>
  );
}
