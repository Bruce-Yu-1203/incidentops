import { connection } from "next/server";
import { Suspense } from "react";

import { InboxView } from "./inbox-view";
import { parseTab } from "./incidents/[id]/pane";

type Search = Promise<Record<string, string | string[] | undefined>>;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

async function Home({ searchParams }: { searchParams: Search }) {
  await connection();
  const params = await searchParams;
  return <InboxView selectedId={null} tab={parseTab(one(params.tab))} flash={{}} />;
}

export default function HomePage({ searchParams }: { searchParams: Search }) {
  return (
    <Suspense fallback={<main className="page">Loading inbox…</main>}>
      <Home searchParams={searchParams} />
    </Suspense>
  );
}
