"use client";

import { useSearchParams } from "next/navigation";
import { type MouseEvent, type ReactNode } from "react";

import { INCIDENT_TABS, incidentTabHref, parseIncidentTab, type IncidentTab } from "@/lib/incident-tabs";

/** Server-rendered panels stay mounted; tab navigation only changes local history. */
export function IncidentTabs({ incidentId, panels }: {
  incidentId: string;
  panels: Record<IncidentTab, ReactNode>;
}) {
  const params = useSearchParams();
  const selected = parseIncidentTab(params.get("tab"));

  function navigate(event: MouseEvent<HTMLDivElement>) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const anchor = (event.target as Element).closest<HTMLAnchorElement>("a[href]");
    if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) return;
    const url = new URL(anchor.href);
    if (url.origin !== window.location.origin) return;
    const local = anchor.getAttribute("href")?.startsWith("?");
    if (!local && url.pathname !== `/incidents/${incidentId}`) return;
    if (!local && !url.searchParams.has("tab")) return;
    event.preventDefault();
    event.stopPropagation();
    const tab = parseIncidentTab(url.searchParams.get("tab"));
    window.history.pushState(null, "", incidentTabHref(incidentId, tab, url.hash));
    if (url.hash) {
      requestAnimationFrame(() => {
        const target = document.getElementById(url.hash.slice(1));
        if (target instanceof HTMLDetailsElement) target.open = true;
        target?.scrollIntoView({ block: "nearest" });
      });
    }
  }

  return (
    <div onClickCapture={navigate} className="incident-tabs-content">
      <nav className="tabs" aria-label="Incident views" role="tablist" onKeyDown={(event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const index = INCIDENT_TABS.findIndex((tab) => tab.key === selected);
        const next = event.key === "Home" ? 0 : event.key === "End" ? INCIDENT_TABS.length - 1
          : (index + (event.key === "ArrowRight" ? 1 : -1) + INCIDENT_TABS.length) % INCIDENT_TABS.length;
        const tab = INCIDENT_TABS[next];
        window.history.pushState(null, "", incidentTabHref(incidentId, tab.key));
        document.getElementById(`tab-${tab.key}`)?.focus();
      }}>
        {INCIDENT_TABS.map((tab) => (
          <a key={tab.key} id={`tab-${tab.key}`} href={incidentTabHref(incidentId, tab.key)}
            role="tab" tabIndex={selected === tab.key ? 0 : -1} aria-selected={selected === tab.key} aria-controls={`panel-${tab.key}`}
            aria-current={selected === tab.key ? "page" : undefined}>
            {tab.label}
          </a>
        ))}
      </nav>
      {INCIDENT_TABS.map((tab) => (
        <section key={tab.key} id={`panel-${tab.key}`} role="tabpanel" aria-labelledby={`tab-${tab.key}`}
          hidden={selected !== tab.key}>
          {panels[tab.key]}
        </section>
      ))}
    </div>
  );
}
