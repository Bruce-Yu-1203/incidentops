import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { PrintButton } from "@/components/print-button";
import { StateBadge, ToneIcon } from "@/components/state-badge";
import { type ActionDetail, fetchAction } from "@/lib/actions";
import { apiBaseUrl } from "@/lib/api";
import { fetchIncident } from "@/lib/incidents";
import { buildReport } from "@/lib/report";
import { sessionFetch } from "@/lib/session";

async function ReportView({ params }: { params: Promise<{ id: string }> }) {
  await connection();
  const { id } = await params;
  const authed = await sessionFetch();
  const result = await fetchIncident(apiBaseUrl(), id, authed);
  if (!result.ok) return <p className="error">Could not load incident: {result.error}</p>;
  const incident = result.data;
  const loaded = await Promise.all(
    incident.actions.map((a) => fetchAction(apiBaseUrl(), a.id, authed)),
  );
  const actions = loaded
    .filter((r): r is { ok: true; data: ActionDetail } => r.ok)
    .map((r) => r.data);
  const missing = loaded.length - actions.length;
  const report = buildReport(incident, actions);
  const d = incident.diagnosis?.diagnosis ?? null;

  return (
    <>
      <p className="crumb no-print">
        <Link href={`/incidents/${incident.id}`}>← Incident</Link>
      </p>
      <section className="panel stack" aria-labelledby="report-title">
        <div className="row-between">
          <div className="stack-sm">
            <span className="eyebrow">Incident report{report.final ? "" : " · provisional"}</span>
            <h1 id="report-title">{incident.dedup_key}</h1>
            <span className="subtitle">
              {incident.service_id} · {incident.environment} · {incident.severity} ·{" "}
              <code>{incident.id}</code>
            </span>
          </div>
          <StateBadge machine="incident" state={incident.state} />
        </div>
        <p className={`outcome tone-${report.tone} report-headline`}>
          <span className="outcome-icon">
            <ToneIcon tone={report.tone} size={16} />
          </span>
          <strong>{report.headline}</strong>
        </p>
        <PrintButton />
      </section>

      <section className="panel stack" aria-labelledby="milestones-heading">
        <h2 id="milestones-heading">Milestones</h2>
        <div className="table-box flat">
          <table>
            <thead>
              <tr>
                <th>Milestone</th>
                <th>Time</th>
                <th>Since alert</th>
              </tr>
            </thead>
            <tbody>
              {report.milestones.map((m) => (
                <tr key={m.label}>
                  <td>{m.label}</td>
                  <td>
                    {m.at ? <time dateTime={m.at}>{new Date(m.at).toLocaleString()}</time> : "—"}
                  </td>
                  <td>{m.sinceOpen ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel stack" aria-labelledby="cause-heading">
        <h2 id="cause-heading">Diagnosis</h2>
        {d ? (
          <>
            <p>
              <strong>{d.root_cause_id}</strong> on <code>{d.affected_component}</code> · confidence{" "}
              {d.confidence_level} (categorical)
            </p>
            <p>{d.summary}</p>
            <p className="subtitle small-text">
              {d.supporting_evidence_ids.length} supporting and{" "}
              {(d.contradicting_evidence_ids ?? []).length} contradicting evidence records. A
              diagnosis is the agent&apos;s hypothesis; only a PASSED independent verification
              confirms recovery.
            </p>
            {(d.unresolved_questions ?? []).length > 0 ? (
              <ul className="tight">
                {d.unresolved_questions.map((q) => (
                  <li key={q}>{q}</li>
                ))}
              </ul>
            ) : null}
          </>
        ) : (
          <p className="subtitle">No diagnosis was produced.</p>
        )}
      </section>

      <section className="panel stack" aria-labelledby="actions-heading">
        <h2 id="actions-heading">Actions</h2>
        {report.actions.length === 0 ? (
          <p className="subtitle">No action was proposed.</p>
        ) : (
          report.actions.map((a) => (
            <div key={a.id} className="stack-sm divider">
              <span className="row-wrap">
                <StateBadge machine="action" state={a.state} />
                <Link href={`/actions/${a.id}`} className="mono">
                  {a.title}
                </Link>
              </span>
              <span className="subtitle">
                {a.decision ? `${a.decision} by ${a.approver}` : "No decision recorded"} · hash{" "}
                <code className="hash">{a.hash}</code>
              </span>
              <span className="row-wrap">
                Verification:{" "}
                {a.verification ? (
                  <StateBadge machine="verification" state={a.verification} />
                ) : (
                  <span className="subtitle">not run</span>
                )}
              </span>
              {a.failedChecks.length > 0 ? (
                <ul className="tight error">
                  {a.failedChecks.map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              ) : null}
            </div>
          ))
        )}
        {missing > 0 ? (
          <p className="error">{missing} action record(s) could not be loaded.</p>
        ) : null}
      </section>
    </>
  );
}

export default function ReportPage({ params }: { params: Promise<{ id: string }> }) {
  return (
    <main className="container wide stack-lg">
      <Suspense fallback={<p>Loading report…</p>}>
        <ReportView params={params} />
      </Suspense>
    </main>
  );
}
