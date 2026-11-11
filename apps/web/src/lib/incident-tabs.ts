export const INCIDENT_TABS = [
  { key: "story", label: "Story" },
  { key: "investigation", label: "Investigation" },
  { key: "evidence", label: "Evidence" },
  { key: "timeline", label: "Timeline & alerts" },
] as const;
export type IncidentTab = (typeof INCIDENT_TABS)[number]["key"];

export function parseIncidentTab(value: string | null | undefined): IncidentTab {
  return INCIDENT_TABS.some((tab) => tab.key === value) ? value as IncidentTab : "story";
}

/** Canonical incident links keep the selection stable when the inbox changes. */
export function incidentTabHref(incidentId: string, tab: IncidentTab, hash = ""): string {
  return `/incidents/${incidentId}${tab === "story" ? "" : `?tab=${tab}`}${hash}`;
}
