import Link from "next/link";

import { apiBaseUrl } from "@/lib/api";
import { formatAge } from "@/lib/attention";
import { defaultSelection, groupInbox, type InboxItem, incidentTitle } from "@/lib/inbox";
import { fetchIncidents } from "@/lib/incidents";
import { sessionFetch } from "@/lib/session";
import { LoadError } from "@/app/load-error";

import { IncidentPane, type PaneFlash, type TabKey } from "./incidents/[id]/pane";

function Card({ item, selected, now }: { item: InboxItem; selected: boolean; now: Date }) {
  const { incident } = item;
  const sub =
    incident.state === "AWAITING_APPROVAL"
      ? "Approve or reject the proposed action"
      : incident.owner_username
        ? `Owner: ${incident.owner_username}`
        : incident.service_id;
  return (
    <Link
      href={`/incidents/${incident.id}`}
      className={`inbox-card accent-${item.tone}`}
      aria-current={selected ? "true" : undefined}
    >
      <span className="meta">
        <span className="status">{item.label}</span>
        <span className="age">{formatAge(incident.created_at, now)}</span>
      </span>
      <strong>{incidentTitle(incident)}</strong>
      <span className="sub">{sub}</span>
    </Link>
  );
}

export async function InboxView({
  selectedId,
  tab,
  flash,
}: {
  selectedId: string | null;
  tab: TabKey;
  flash: PaneFlash;
}) {
  const result = await fetchIncidents(apiBaseUrl(), await sessionFetch());
  if (!result.ok) {
    return (
      <main className="page">
        <LoadError what="incidents" error={result.error} next="/" />
      </main>
    );
  }
  const inbox = groupInbox(result.data);
  const selected = selectedId ?? defaultSelection(inbox);
  const now = new Date();
  return (
    <div className="inbox-layout with-inbox">
      <aside className="inbox" aria-label="Incident inbox">
        <span className="inbox-group">Needs you · {inbox.needsYou.length}</span>
        {inbox.needsYou.length === 0 ? <span className="inbox-empty">Nothing needs you right now.</span> : null}
        {inbox.needsYou.map((item) => (
          <Card key={item.incident.id} item={item} selected={item.incident.id === selected} now={now} />
        ))}
        <span className="inbox-group">System is handling · {inbox.handling.length}</span>
        {inbox.handling.length === 0 ? <span className="inbox-empty">No incidents in progress.</span> : null}
        {inbox.handling.map((item) => (
          <Card key={item.incident.id} item={item} selected={item.incident.id === selected} now={now} />
        ))}
        <Link href="/history" className="inbox-footer">
          Search closed incidents in History →
        </Link>
      </aside>
      {selected ? (
        <IncidentPane incidentId={selected} tab={tab} flash={flash} />
      ) : (
        <main className="pane">
          <h1>All quiet.</h1>
          <p className="muted">No open incidents. Closed ones are in History.</p>
        </main>
      )}
    </div>
  );
}
