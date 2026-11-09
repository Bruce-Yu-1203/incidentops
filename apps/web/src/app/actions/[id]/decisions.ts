"use server";

import { redirect } from "next/navigation";

import { type DecisionKind, sendDecision } from "@/lib/actions";
import { apiBaseUrl } from "@/lib/api";
import { sessionFetch } from "@/lib/session";

// The API authenticates the forwarded session and re-validates role, state,
// version, hash and expiry; this function only relays the user's decision.
async function decide(kind: DecisionKind, formData: FormData): Promise<void> {
  const actionId = String(formData.get("action_id") ?? "");
  const result = await sendDecision(
    apiBaseUrl(),
    {
      actionId,
      kind,
      expectedVersion: Number(formData.get("expected_version")),
      actionHash: kind === "approve" ? String(formData.get("action_hash") ?? "") : undefined,
      reason: String(formData.get("reason") ?? "") || undefined,
    },
    await sessionFetch(),
  );
  const status = result.ok ? `done=${result.data.outcome}` : `error=${encodeURIComponent(result.error)}`;
  redirect(`/actions/${encodeURIComponent(actionId)}?${status}`);
}

export async function approveAction(formData: FormData): Promise<void> {
  await decide("approve", formData);
}

export async function rejectAction(formData: FormData): Promise<void> {
  await decide("reject", formData);
}

export async function cancelAction(formData: FormData): Promise<void> {
  await decide("cancel", formData);
}
