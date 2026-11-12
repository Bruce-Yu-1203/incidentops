import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { AutoRefresh } from "@/components/auto-refresh";
import { StateBadge, ToneIcon } from "@/components/state-badge";
import { apiBaseUrl } from "@/lib/api";
import { shortId } from "@/lib/evidence";
import { type EvidenceView, fetchRun, safeSourceUrl } from "@/lib/investigations";
import { runIsLive } from "@/lib/refresh";
import { sessionFetch } from "@/lib/session";
import { evidenceGraph, type GraphNode, stepRow } from "@/lib/trajectory";
import { LoadError } from "@/app/load-error";
import { rootCauseLabel } from "@/lib/vocabulary";

function EvidenceCard({ node, kind }: { node: GraphNode; kind: "supporting" | "contradicting" }) {
  const e = node.evidence;
  return (
    <li className={`graph-node chip-${kind}`}>
      <a href={`#${node.id}`} className="mono small-text" title={node.id}>
        {shortId(node.id)}
      </a>
      {e ? (
        <>
          <span className="subtitle small-text">
            {e.source} · {e.template_id}
          </span>
          <span>{e.summary}</span>
        </>
      ) : (
        <span className="error small-text">Cited but not found in this run.</span>
      )}
    </li>
  );
}

function SourceLink({ item }: { item: EvidenceView }) {
  const href = safeSourceUrl(item.source_url);
  return href ? (
    <a href={href} target="_blank" rel="noreferrer noopener">
      Grafana ↗
    </a>
  ) : (
    <>—</>
  );
}

async function RunView({ params }: { params: Promise<{ id: string }> }) {
  await connection();
  const { id } = await params;
  const result = await fetchRun(apiBaseUrl(), id, await sessionFetch());
  if (!result.ok)
    return <LoadError what="investigation" error={result.error} next={`/investigations/${id}`} />;
  const run = result.data;
  const rows = run.steps.map(stepRow);
  const blocked = rows.filter((r) => r.blocked).length;
  const diagnosis = run.diagnosis?.diagnosis ?? null;
  const graph = evidenceGraph(diagnosis, run.evidence);
  return (
    <>
      <AutoRefresh active={runIsLive(run)} />
      <p className="crumb">
        <Link href={`/incidents/${run.incident_id}`}>← Incident</Link>
      </p>
      <section className="panel stack" aria-labelledby="run-title">
        <div className="row-between">
          <h1 id="run-title">
            Investigation <code>{shortId(run.id)}</code>
          </h1>
          <StateBadge machine="run" state={run.state} />
        </div>
        <div className="fact-grid">
          <div>
            <span className="subtitle">Model</span>
            <span>{run.model_id ?? "—"}</span>
          </div>
          <div>
            <span className="subtitle">Steps</span>
            <span>
              {run.step_count} model · {run.tool_call_count} tool
              {blocked ? ` · ${blocked} blocked` : ""}
            </span>
          </div>
          <div>
            <span className="subtitle">Tokens</span>
            <span>
              {run.input_tokens} in / {run.output_tokens} out
            </span>
          </div>
          {run.error_code ? (
            <div>
              <span className="subtitle">Error</span>
              <span className="error">{run.error_code}</span>
            </div>
          ) : null}
        </div>
        <p className="subtitle small-text">
          A COMPLETED run returned a valid diagnosis, which may still report uncertainty. It does
          not mean the incident is resolved.
        </p>
      </section>

      <section className="panel stack" aria-labelledby="graph-heading">
        <h2 id="graph-heading">Evidence graph</h2>
        {diagnosis ? (
          <div className="graph">
            <div className="graph-col">
              <span className="eyebrow tone-success-text">
                Supports · {graph.supporting.length}
              </span>
              <ul className="graph-list">
                {graph.supporting.map((n) => (
                  <EvidenceCard key={n.id} node={n} kind="supporting" />
                ))}
              </ul>
            </div>
            <div className="graph-center">
              <span className="eyebrow">Diagnosis (hypothesis)</span>
              <strong title={diagnosis.root_cause_id}>{rootCauseLabel(diagnosis.root_cause_id)}</strong>
              <code className="small-text">{diagnosis.affected_component}</code>
              <span className="subtitle small-text">confidence {diagnosis.confidence_level}</span>
            </div>
            <div className="graph-col">
              <span className="eyebrow tone-unknown-text">
                Contradicts · {graph.contradicting.length}
              </span>
              {graph.contradicting.length === 0 ? (
                <span className="subtitle small-text">No contradicting evidence recorded.</span>
              ) : (
                <ul className="graph-list">
                  {graph.contradicting.map((n) => (
                    <EvidenceCard key={n.id} node={n} kind="contradicting" />
                  ))}
                </ul>
              )}
            </div>
          </div>
        ) : (
          <p className="subtitle">No diagnosis in this run yet.</p>
        )}
        {graph.uncited.length > 0 ? (
          <p className="subtitle small-text">
            {graph.uncited.length} collected record{graph.uncited.length === 1 ? " is" : "s are"}{" "}
            not cited by the diagnosis.
          </p>
        ) : null}
      </section>

      <section className="panel stack" aria-labelledby="trajectory-heading">
        <h2 id="trajectory-heading">Trajectory</h2>
        <p className="subtitle small-text">
          Observable model and tool steps only; no hidden reasoning. Tool arguments are shown as
          recorded after redaction.
        </p>
        <ol className="trajectory">
          {rows.map((row) => (
            <li
              key={row.id}
              className={
                row.blocked ? `traj-row traj-blocked tone-${row.tone}` : `traj-row tone-${row.tone}`
              }
            >
              <span className="vt-icon">
                <ToneIcon tone={row.tone} />
              </span>
              <span className="vt-body">
                <span>
                  <strong>{row.title}</strong> <span className="subtitle">· {row.kind}</span>
                </span>
                <span className="subtitle">{row.detail}</span>
                {row.args ? <code className="small-text args">{row.args}</code> : null}
              </span>
              <span className="subtitle small-text traj-duration">{row.duration ?? "running"}</span>
            </li>
          ))}
        </ol>
      </section>

      <section className="panel stack" aria-labelledby="evidence-heading">
        <h2 id="evidence-heading">Evidence records</h2>
        <div className="table-box flat">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Source</th>
                <th>Query</th>
                <th>Summary</th>
                <th>Link</th>
              </tr>
            </thead>
            <tbody>
              {run.evidence.map((item) => (
                <tr key={item.id} id={item.id} className="anchor-row">
                  <td>
                    <code title={item.id}>{shortId(item.id)}</code>
                  </td>
                  <td>{item.source}</td>
                  <td>{item.template_id}</td>
                  <td>
                    {item.summary}
                    {item.truncated ? " (truncated)" : ""}
                  </td>
                  <td>
                    <SourceLink item={item} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

export default function InvestigationPage({ params }: { params: Promise<{ id: string }> }) {
  return (
    <main className="container wide stack-lg">
      <Suspense fallback={<p>Loading investigation…</p>}>
        <RunView params={params} />
      </Suspense>
    </main>
  );
}
