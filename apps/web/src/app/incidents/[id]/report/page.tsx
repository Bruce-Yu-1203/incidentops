import { redirect } from "next/navigation";
import { Suspense } from "react";

import { isIncidentId } from "@/lib/incidents";

async function Redirect({ params }: { params: Promise<{ id: string }> }): Promise<null> {
  const { id } = await params;
  redirect(isIncidentId(id) ? `/reports?id=${id}` : "/reports");
}

/** Reports moved to /reports; keep old links working. */
export default function LegacyReportPage({ params }: { params: Promise<{ id: string }> }) {
  return (
    <Suspense fallback={null}>
      <Redirect params={params} />
    </Suspense>
  );
}
