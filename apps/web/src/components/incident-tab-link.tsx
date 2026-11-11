"use client";

import type { ReactNode } from "react";
import { incidentTabHref, type IncidentTab } from "@/lib/incident-tabs";

/** Local detail links also work outside the tab panel's navigation boundary. */
export function IncidentTabLink({ incidentId, tab, hash = "", className, children }: {
  incidentId: string; tab: IncidentTab; hash?: string; className?: string; children: ReactNode;
}) {
  const href = incidentTabHref(incidentId, tab, hash);
  return <a href={href} className={className} onClick={(event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    window.history.pushState(null, "", href);
    requestAnimationFrame(() => {
      const target = document.getElementById(hash.slice(1));
      if (target instanceof HTMLDetailsElement) target.open = true;
      target?.scrollIntoView({ block: "nearest" });
    });
  }}>{children}</a>;
}
