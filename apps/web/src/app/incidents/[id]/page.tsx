import { connection } from "next/server";
import { Suspense } from "react";

import { InboxView } from "@/app/inbox-view";

import { parseTab } from "./pane";

type Search = Promise<Record<string, string | string[] | undefined>>;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

async function IncidentView({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Search }) {
  await connection();
  const [{ id }, query] = await Promise.all([params, searchParams]);
  return (
    <InboxView
      selectedId={id}
      tab={parseTab(one(query.tab))}
      flash={{ done: one(query.done), error: one(query.error), recheck: one(query.recheck) }}
    />
  );
}

export default function IncidentPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Search;
}) {
  return (
    <Suspense fallback={<main className="page">Loading incident…</main>}>
      <IncidentView params={params} searchParams={searchParams} />
    </Suspense>
  );
}
