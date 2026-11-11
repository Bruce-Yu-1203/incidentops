"use server";

import { redirect } from "next/navigation";

import { apiBaseUrl } from "@/lib/api";
import { assignIncident, closeIncident, encodeRecheck, recheckIncident } from "@/lib/handling";
import { isIncidentId } from "@/lib/incidents";
import { sessionFetch } from "@/lib/session";

// The API authenticates the forwarded session and re-checks role, state and
// version; these functions only relay what the person chose.

function target(formData: FormData): { id: string; version: number } {
  const id = String(formData.get("incident_id") ?? "");
  if (!isIncidentId(id)) redirect("/");
  return { id, version: Number(formData.get("expected_version")) };
}

const back = (id: string, params: Record<string, string>) =>
  `/incidents/${id}?${new URLSearchParams(params).toString()}`;

export async function assignToMe(formData: FormData): Promise<void> {
  const { id, version } = target(formData);
  const result = await assignIncident(apiBaseUrl(), id, version, await sessionFetch());
  redirect(back(id, result.ok ? { done: "assigned" } : { error: result.error }));
}

export async function closeHandover(formData: FormData): Promise<void> {
  const { id, version } = target(formData);
  const note = String(formData.get("note") ?? "").trim() || undefined;
  const result = await closeIncident(
    apiBaseUrl(),
    id,
    version,
    String(formData.get("disposition") ?? ""),
    note,
    await sessionFetch(),
  );
  redirect(back(id, result.ok ? { done: "closed" } : { error: result.error }));
}

export async function recheckNow(formData: FormData): Promise<void> {
  const { id } = target(formData);
  const result = await recheckIncident(apiBaseUrl(), id, await sessionFetch());
  redirect(back(id, result.ok ? { recheck: encodeRecheck(result.data) } : { error: result.error }));
}
