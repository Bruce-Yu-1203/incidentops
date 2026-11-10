import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { AutoRefresh } from "@/components/auto-refresh";
import { StateBadge } from "@/components/state-badge";
import { apiBaseUrl } from "@/lib/api";
import { runIsLive } from "@/lib/refresh";
import { sessionFetch } from "@/lib/session";
import { fetchRun, safeSourceUrl } from "@/lib/investigations";

function describeStep(metadata: Record<string, unknown>): string {
  if (typeof metadata.tool === "string") {
    const evidence = typeof metadata.evidence_id === "string" ? ` → ${metadata.evidence_id}` : "";
    const error = typeof metadata.error_code === "string" ? ` (${metadata.error_code})` : "";
    return `${metadata.tool}${evidence}${error}`;
  }
  if (Array.isArray(metadata.tool_calls)) {
    return `model requested: ${metadata.tool_calls.join(", ") || "no tools"}`;
  }
  if (typeof metadata.root_cause_id === "string") return `diagnosis ${metadata.root_cause_id}`;
  return typeof metadata.error === "string" ? metadata.error : "";
}

async function RunView({ params }: { params: Promise<{ id: string }> }) {
  await connection();
  const { id } = await params;
  const result = await fetchRun(apiBaseUrl(), id, await sessionFetch());
  if (!result.ok) return <p className="error">Could not load investigation: {result.error}</p>;
  const run = result.data;
  return (
    <>
      <AutoRefresh active={runIsLive(run)} />
      <h1>Investigation {run.id}</h1>
      <p>
        <Link href={`/incidents/${run.incident_id}`}>← Incident</Link>
      </p>
      <dl className="facts">
        <dt>State</dt>
        <dd>
          <StateBadge machine="run" state={run.state} />
          {run.error_code ? ` (${run.error_code})` : ""}
        </dd>
        <dt>Model</dt>
        <dd>{run.model_id ?? "—"}</dd>
        <dt>Usage</dt>
        <dd>
          {run.step_count} model steps · {run.tool_call_count} tool calls · {run.input_tokens} in /{" "}
          {run.output_tokens} out tokens
        </dd>
      </dl>

      <h2>Trajectory</h2>
      <p className="subtitle">Observable model and tool steps only; no hidden reasoning.</p>
      <ol className="timeline">
        {run.steps.map((step) => (
          <li key={step.id}>
            <time dateTime={step.started_at}>{new Date(step.started_at).toLocaleTimeString()}</time>{" "}
            · <strong>{step.kind}</strong> · <StateBadge machine="step" state={step.status} /> ·{" "}
            {describeStep(step.metadata)}
          </li>
        ))}
      </ol>

      <h2>Evidence</h2>
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
          {run.evidence.map((item) => {
            const href = safeSourceUrl(item.source_url);
            return (
              <tr key={item.id} id={item.id}>
                <td>
                  <code>{item.id.slice(0, 11)}…</code>
                </td>
                <td>{item.source}</td>
                <td>{item.template_id}</td>
                <td>
                  {item.summary}
                  {item.truncated ? " (truncated)" : ""}
                </td>
                <td>
                  {href ? (
                    <a href={href} target="_blank" rel="noreferrer noopener">
                      Grafana
                    </a>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}

export default function InvestigationPage({ params }: { params: Promise<{ id: string }> }) {
  return (
    <main className="container">
      <Suspense fallback={<p>Loading investigation…</p>}>
        <RunView params={params} />
      </Suspense>
    </main>
  );
}
