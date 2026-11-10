import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { fetchAction } from "@/lib/actions";
import { StateBadge } from "@/components/state-badge";
import { apiBaseUrl } from "@/lib/api";
import { fetchMe } from "@/lib/auth";
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
  const canCancel = CANCELLABLE.has(action.state) && user !== null && user.role !== "viewer";
  const hidden = (
    <>
      <input type="hidden" name="action_id" value={action.id} />
      <input type="hidden" name="expected_version" value={action.version} />
    </>
  );

  return (
    <>
      <p>
        <Link href={`/incidents/${action.incident_id}`}>← Incident</Link>
      </p>
      <h1>
        {String(envelope.action_type)} → {String(envelope.target_id)}
      </h1>
      {done ? <p className="ok">Decision recorded: {done}</p> : null}
      {error ? <p className="error">Decision refused: {error}</p> : null}

      <h2>Exact action to authorize</h2>
      <p className="subtitle">
        Approval binds to this hash. Any change to type, target, environment, arguments, target
        revision, policy version or expiry produces a different hash and is refused.
      </p>
      <dl className="facts">
        <dt>State</dt>
        <dd>
          <StateBadge machine="action" state={action.state} /> (version {action.version})
          {action.reason_code ? ` · ${action.reason_code}` : ""}
        </dd>
        <dt>Environment</dt>
        <dd>{String(envelope.environment)}</dd>
        <dt>Arguments</dt>
        <dd>
          <code>{JSON.stringify(envelope.arguments)}</code>
        </dd>
        <dt>Target revision</dt>
        <dd>
          <code>{String(envelope.target_revision)}</code>
        </dd>
        <dt>Policy</dt>
        <dd>{action.policy_version}</dd>
        <dt>Expires</dt>
        <dd>
          <time dateTime={action.expires_at}>{new Date(action.expires_at).toLocaleString()}</time>
        </dd>
        <dt>Requested by</dt>
        <dd>{String(envelope.requested_by)}</dd>
        <dt>Evidence</dt>
        <dd>
          {Array.isArray(envelope.evidence_ids) ? envelope.evidence_ids.length : 0} cited records
        </dd>
        <dt>Action hash</dt>
        <dd>
          <code className="hash">{action.action_hash}</code>
        </dd>
      </dl>

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
        <p>{user ? "Only approvers can decide." : <Link href={`/login?next=/actions/${action.id}`}>Sign in to decide.</Link>}</p>
      ) : null}
      {canCancel ? (
        <form action={cancelAction}>
          {hidden}
          <button type="submit" className="secondary">
            Cancel before dispatch
          </button>
        </form>
      ) : null}

      <h2>Decisions</h2>
      {action.decisions.length === 0 ? (
        <p>None yet.</p>
      ) : (
        <ul>
          {action.decisions.map((d) => (
            <li key={`${d.approver_id}-${d.decided_at}`}>
              {d.decision} by {d.approver_id} at {new Date(d.decided_at).toLocaleString()}
              {d.reason ? ` — ${d.reason}` : ""}
            </li>
          ))}
        </ul>
      )}

      <h2>Execution</h2>
      {action.operations.length === 0 ? (
        <p>Not dispatched.</p>
      ) : (
        <ul>
          {action.operations.map((o) => (
            <li key={o.provider}>
              {o.provider} · operation {o.provider_operation_id} ·{" "}
              <StateBadge machine="step" state={o.status} />
            </li>
          ))}
        </ul>
      )}
      {action.state === "RECONCILING" ? (
        <p className="error">Outcome unknown: reconciling by action ID; it will not be re-sent.</p>
      ) : null}

      <h2>Independent verification</h2>
      {action.verifications.length === 0 ? (
        <p>Not verified.</p>
      ) : (
        action.verifications.map((v) => (
          <div key={v.id} className="panel">
            <p>
              <StateBadge machine="verification" state={v.outcome} />
            </p>
            <ul>
              {v.checks.map((c) => (
                <li key={c.check_id}>
                  {c.check_id}: {c.passed === null ? "unknown" : c.passed ? "pass" : "fail"} —{" "}
                  {c.observed}
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </>
  );
}

export default function ActionPage(props: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ done?: string; error?: string }>;
}) {
  return (
    <main className="container">
      <Suspense fallback={<p>Loading action…</p>}>
        <ActionView {...props} />
      </Suspense>
    </main>
  );
}
