import { connection } from "next/server";
import { Suspense } from "react";

import { apiBaseUrl } from "@/lib/api";
import { CASE_NAMES, fetchBenchmarks, runSummary, unmeasuredCases } from "@/lib/benchmarks";
import { formatSeconds } from "@/lib/history";
import { sessionFetch } from "@/lib/session";
import { LoadError } from "@/app/load-error";

async function BenchmarksView() {
  await connection();
  const result = await fetchBenchmarks(apiBaseUrl(), await sessionFetch());
  if (!result.ok) return <LoadError what="benchmarks" error={result.error} next="/benchmarks" />;
  const run = result.data[0];
  if (!run)
    return (
      <>
        <p className="muted">No published benchmark runs yet.</p>
        <NotRunYet cases={unmeasuredCases(null)} />
      </>
    );
  const s = runSummary(run);
  const failing = run.cases.filter((c) => c.passed < c.trials.length);
  return (
    <>
      <p className="small muted" style={{ margin: 0 }}>
        Run <span className="mono">{run.run_id}</span> · {run.variant} · model <span className="mono">{run.model_id}</span> ·{" "}
        {run.trials_per_case} trials per scenario · {new Date(run.started_at).toLocaleString()}
      </p>
      <div className="stats">
        <div className="stat-card">
          <span className="label">Trials passed</span>
          <span className="big">
            {s.passed}/{s.attempted}
          </span>
        </div>
        <div className="stat-card">
          <span className="label">Unsafe actions executed</span>
          <span className="big">{s.unsafe}</span>
        </div>
        <div className="stat-card">
          <span className="label">Median time to detect</span>
          <span className="big">{formatSeconds(s.medianTtd)}</span>
        </div>
        <div className="stat-card">
          <span className="label">Scenarios fully passing</span>
          <span className="big">
            {s.scenariosAllPass} of {s.scenarios}
          </span>
        </div>
      </div>
      <div className="table-scroll">
        <table className="ctable">
          <thead>
            <tr>
              <th>Scenario</th>
              <th>Trials</th>
              <th>Passed</th>
              <th>Median detect</th>
            </tr>
          </thead>
          <tbody>
            {run.cases.map((c) => (
              <tr key={c.case_id}>
                <td>
                  <strong>{c.case_id}</strong> <span className="muted">{CASE_NAMES[c.case_id] ?? ""}</span>
                </td>
                <td>
                  <span className="trials" aria-label={`${c.passed} of ${c.trials.length} passed`}>
                    {c.trials.map((t) => (
                      <span
                        key={t.trial_id}
                        className={t.status === "PASS" ? "" : t.status === "INVALID" ? "invalid" : "fail"}
                        title={`${t.trial_id}: ${t.status}${t.reasons.length ? ` (${t.reasons.join(", ")})` : ""}`}
                      />
                    ))}
                  </span>
                </td>
                <td className={c.passed < c.trials.length ? "" : "muted"} style={c.passed < c.trials.length ? { color: "var(--danger)", fontWeight: 600 } : undefined}>
                  {c.passed}/{c.trials.length}
                </td>
                <td className="small">{formatSeconds(c.median_time_to_detect_s)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {failing.map((c) => (
        <section key={c.case_id} className="card">
          <h2 style={{ fontSize: 18 }}>
            Why {c.case_id} failed {c.trials.length - c.passed} of {c.trials.length}
          </h2>
          {c.case_id === "B-F04" ? (
            <p className="small">
              The memory-leak fixture is capped at 400 MB. In the failed trials memory had not grown enough when the rules
              baseline looked, so it answered “insufficient evidence” and proposed nothing. That is a safe abstention, not an
              unsafe action, but it misses the leak. Removing the cap or giving the leak more time is the planned fix.
            </p>
          ) : null}
          <ul className="plain-list">
            {c.trials
              .filter((t) => t.status !== "PASS")
              .map((t) => (
                <li key={t.trial_id}>
                  <span className="mono tiny">{t.trial_id}</span>: {t.reasons.join(", ") || t.status}
                </li>
              ))}
          </ul>
        </section>
      ))}
      <div className="care">
        <strong>Read with care.</strong> Five trials per scenario in one disposable Docker lab is a smoke-level sample: a
        5/5 has a 95% interval of roughly 57–100%. Restart, rollback and CPU faults are simulated. These numbers show the
        loop works end to end and stays safe; they do not compare models.
      </div>
      <NotRunYet cases={unmeasuredCases(run)} />
      <div className="placeholder">
        <strong>B1 · model-driven agent</strong>
        <br />
        Not run yet. When a B1 run is published in <span className="mono">docs/benchmark-results</span> it appears here
        next to B0.
      </div>
    </>
  );
}

function NotRunYet({ cases }: { cases: { id: string; name: string }[] }) {
  if (cases.length === 0) return null;
  return (
    <section className="panel stack" aria-labelledby="not-run-heading">
      <h2 id="not-run-heading">Scenarios without a published run</h2>
      <p className="small muted" style={{ margin: 0 }}>
        These scenarios exist in the fault lab but have no committed results yet, so no numbers are shown for them.
      </p>
      <ul className="small">
        {cases.map((c) => (
          <li key={c.id}>
            <strong>{c.id}</strong> <span className="muted">{c.name}</span> · not run yet
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function BenchmarksPage() {
  return (
    <main className="page">
      <div>
        <h1>Benchmarks</h1>
        <p className="muted" style={{ margin: "4px 0 0" }}>
          Measured runs of the fault lab scenarios. Only committed results are shown.
        </p>
      </div>
      <Suspense fallback={<p>Loading benchmarks…</p>}>
        <BenchmarksView />
      </Suspense>
    </main>
  );
}
