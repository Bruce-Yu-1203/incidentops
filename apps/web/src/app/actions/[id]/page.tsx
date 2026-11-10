import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { CopyButton } from "@/components/copy-button";
import { Countdown } from "@/components/countdown";
import { OutcomeBanner } from "@/components/outcome-banner";
import { StateBadge } from "@/components/state-badge";
import { Stepper } from "@/components/stepper";
import { fetchAction } from "@/lib/actions";
import { apiBaseUrl } from "@/lib/api";
import { fetchMe } from "@/lib/auth";
import { shortHash } from "@/lib/countdown";
import { shortId } from "@/lib/evidence";
import { actionLifecycle } from "@/lib/lifecycle";
import { actionOutcome, sortChecks } from "@/lib/outcomes";
import { sessionFetch } from "@/lib/session";

import { approveAction, cancelAction, rejectAction } from "./decisions";

const CANCELLABLE = new Set(["PENDING_APPROVAL", "APPROVED", "QUEUED"]);

async function ActionView({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ done?: string; error?: string }>;
}) {
  await connection();
  const [{ id }, { done, error }] = await Promise.all([params, searchParams]);
  const authed = await sessionFetch();
  const [result, user] = await Promise.all([
    fetchAction(apiBaseUrl(), id, authed),
    fetchMe(apiBaseUrl(), authed),
  ]);
  if (!result.ok) return <p className="error">Could not load action: {result.error}</p>;
  const action = result.data;
  const envelope = action.envelope;
  const pending = action.state === "PENDING_APPROVAL";
  const canDecide = pending && user?.role === "approver";
  const outcome = actionOutcome(action);
  const lastOperation = action.operations.at(-1);
  const canCancel = CANCELLABLE.has(action.state) && user !== null && user.role !== "viewer";
  const evidenceIds = Array.isArray(envelope.evidence_ids)
    ? envelope.evidence_ids.filter((e): e is string => typeof e === "string")
    : [];
  const args =
    envelope.arguments && typeof envelope.arguments === "object" ? envelope.arguments : {};
  const hidden = (
    <>
      <input type="hidden" name="action_id" value={action.id} />
      <input type="hidden" name="expected_version" value={action.version} />
    </>
  );

  return (
    <>
      <p className="crumb">
        <Link href={`/incidents/${action.incident_id}`}>← Incident</Link>
      </p>
      <section className="panel stack" aria-labelledby="action-title">
        <div className="row-between">
          <h1 id="action-title" className="mono">
            {String(envelope.action_type)} → {String(envelope.target_id)}
          </h1>
          <StateBadge machine="action" state={action.state} />
        </div>
        <Stepper
          label="Action lifecycle"
          lifecycle={actionLifecycle(
            action.state,
            action.verifications.at(-1)?.outcome ?? null,
            action.decisions.some((d) => d.decision.toUpperCase().startsWith("APPROVE")),
          )}
        />
        {outcome ? (
          <OutcomeBanner outcome={outcome}>
            {outcome.kind === "reconciling" && lastOperation ? (
              <span className="subtitle">
                Provider {lastOperation.provider} · operation{" "}
                <code>{lastOperation.provider_operation_id ?? "not yet known"}</code> · last update{" "}
                <time dateTime={lastOperation.updated_at}>
                  {new Date(lastOperation.updated_at).toLocaleTimeString()}
                </time>
              </span>
            ) : null}
          </OutcomeBanner>
        ) : null}
      </section>
      {done ? <p className="ok">Decision recorded: {done}</p> : null}
      {error ? <p className="error">Decision refused: {error}</p> : null}

      <div className="columns">
        <div className="col-main stack-lg">
          <section
            className={pending ? "panel stack panel-attention" : "panel stack"}
            aria-labelledby="exact-heading"
          >
            <h2 id="exact-heading">
              {pending ? "You are being asked to approve" : "Exact action"}
            </h2>
            <p className="lead">
              <strong className="mono">{String(envelope.action_type)}</strong> on{" "}
              <code>{String(envelope.target_id)}</code> in{" "}
              <strong>{String(envelope.environment)}</strong>
            </p>
            <div className="fact-grid">
              <div>
                <span className="subtitle">Expires</span>
                {pending ? (
                  <Countdown expiresAt={action.expires_at} />
                ) : (
                  <time dateTime={action.expires_at}>
                    {new Date(action.expires_at).toLocaleString()}
                  </time>
                )}
              </div>
              <div>
                <span className="subtitle">Policy</span>
                <span>{action.policy_version}</span>
              </div>
              <div>
                <span className="subtitle">Requested by</span>
                <span>{String(envelope.requested_by)}</span>
              </div>
              <div>
                <span className="subtitle">Target revision</span>
                <code>{String(envelope.target_revision)}</code>
              </div>
            </div>
            <div className="stack-sm">
              <span className="eyebrow">Arguments</span>
              {Object.keys(args).length === 0 ? (
                <span className="subtitle">None.</span>
              ) : (
                <dl className="facts">
                  {Object.entries(args).map(([key, value]) => (
                    <div key={key} className="contents">
                      <dt>{key}</dt>
                      <dd>
                        <code>{JSON.stringify(value)}</code>
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
            </div>
            <div className="stack-sm">
              <span className="eyebrow">Cited evidence · {evidenceIds.length}</span>
              <div className="chips">
                {evidenceIds.map((e) => (
                  <span key={e} className="chip chip-supporting" title={e}>
                    <code>{shortId(e)}</code>
                  </span>
                ))}
              </div>
            </div>
            <div className="stack-sm divider">
              <span className="eyebrow">Action hash</span>
              <span className="row-wrap">
                <code className="hash" title={action.action_hash}>
                  {action.action_hash}
                </code>
                <CopyButton value={action.action_hash} label="Copy action hash" />
              </span>
              <span className="subtitle small-text">
                Approval binds to this hash ({shortHash(action.action_hash)}). Any change to type,
                target, environment, arguments, target revision, policy version or expiry produces a
                different hash and is refused.
              </span>
              {action.reason_code ? (
                <span className="subtitle">Reason code: {action.reason_code}</span>
              ) : null}
              <span className="subtitle small-text">Record version {action.version}</span>
            </div>
            {canDecide ? (
              <div className="decisions">
                <form action={approveAction} className="stack">
                  {hidden}
                  <input type="hidden" name="action_hash" value={action.action_hash} />
                  <label>
                    Reason (optional)
                    <input name="reason" maxLength={2000} />
                  </label>
                  <button type="submit">Approve this exact action</button>
                </form>
                <form action={rejectAction} className="stack">
                  {hidden}
                  <label>
                    Reason (optional)
                    <input name="reason" maxLength={2000} />
                  </label>
                  <button type="submit" className="secondary">
                    Reject
                  </button>
                </form>
              </div>
            ) : pending ? (
              <p>
                {user ? (
                  "Only approvers can decide."
                ) : (
                  <Link href={`/login?next=/actions/${action.id}`}>Sign in to decide.</Link>
                )}
              </p>
            ) : null}
            {canCancel ? (
              <form action={cancelAction}>
                {hidden}
                <button type="submit" className="secondary">
                  Cancel before dispatch
                </button>
              </form>
            ) : null}
          </section>
        </div>

        <aside className="col-side stack-lg" aria-label="Action history">
          <section className="panel stack">
            <h2>Decisions</h2>
            {action.decisions.length === 0 ? (
              <p className="subtitle">None yet.</p>
            ) : (
              <ul className="tight">
                {action.decisions.map((d) => (
                  <li key={`${d.approver_id}-${d.decided_at}`}>
                    {d.decision} by {d.approver_id} at {new Date(d.decided_at).toLocaleString()}
                    {d.reason ? ` — ${d.reason}` : ""}
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="panel stack">
            <h2>Execution</h2>
            {action.operations.length === 0 ? (
              <p className="subtitle">Not dispatched.</p>
            ) : (
              <ul className="tight">
                {action.operations.map((o) => (
                  <li key={o.provider}>
                    {o.provider} · operation {o.provider_operation_id} ·{" "}
                    <StateBadge machine="step" state={o.status} />
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="panel stack">
            <h2>Independent verification</h2>
            {action.verifications.length === 0 ? (
              <p className="subtitle">
                Not verified yet. It starts only after the action outcome is confirmed.
              </p>
            ) : (
              action.verifications.map((v) => (
                <div key={v.id} className="stack-sm">
                  <p>
                    <StateBadge machine="verification" state={v.outcome} />{" "}
                    <span className="subtitle">
                      window{" "}
                      <time dateTime={v.window_start}>
                        {new Date(v.window_start).toLocaleTimeString()}
                      </time>
                      –
                      <time dateTime={v.window_end}>
                        {new Date(v.window_end).toLocaleTimeString()}
                      </time>
                    </span>
                  </p>
                  <ul className="checks">
                    {sortChecks(v.checks).map((c) => (
                      <li key={c.check_id}>
                        <StateBadge
                          machine="verification"
                          state={
                            c.passed === null
                              ? "UNKNOWN"
                              : c.passed
                                ? "PASSED"
                                : "VERIFICATION_FAILED"
                          }
                          label={c.passed === null ? "unknown" : c.passed ? "pass" : "fail"}
                        />
                        <span className="check-id">{c.check_id}</span>
                        <span>{c.observed}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ))
            )}
          </section>
        </aside>
      </div>
    </>
  );
}

export default function ActionPage(props: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ done?: string; error?: string }>;
}) {
  return (
    <main className="container wide stack-lg">
      <Suspense fallback={<p>Loading action…</p>}>
        <ActionView {...props} />
      </Suspense>
    </main>
  );
}
