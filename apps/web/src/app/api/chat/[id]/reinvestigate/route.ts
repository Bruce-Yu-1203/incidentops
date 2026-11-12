import { NextResponse } from "next/server";

import { apiBaseUrl } from "@/lib/api";
import { isIncidentId } from "@/lib/incidents";
import { sessionFetch } from "@/lib/session";

// Same-origin relay for "Start re-investigation" in the chat drawer (ADR-023).
// The API decides whether the incident may be re-investigated and who may ask.

type Params = { params: Promise<{ id: string }> };

export async function POST(request: Request, { params }: Params) {
  const { id } = await params;
  if (!isIncidentId(id)) return NextResponse.json({ detail: "invalid incident id" }, { status: 400 });
  const body: unknown = await request.json().catch(() => null);
  const fields = typeof body === "object" && body !== null ? (body as Record<string, unknown>) : {};
  const reason = typeof fields.reason === "string" ? fields.reason.slice(0, 400) : null;
  const fromMessage =
    typeof fields.from_message_id === "string" && /^msg_[0-9a-zA-Z]+$/.test(fields.from_message_id)
      ? fields.from_message_id
      : null;
  const authed = await sessionFetch();
  try {
    const response = await authed(`${apiBaseUrl()}/api/v1/incidents/${id}/reinvestigate`, {
      method: "POST",
      cache: "no-store",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ reason, from_message_id: fromMessage }),
    });
    const result: unknown = await response.json().catch(() => ({}));
    return NextResponse.json(result, { status: response.status });
  } catch {
    return NextResponse.json({ detail: "API unavailable" }, { status: 502 });
  }
}
