import { NextResponse } from "next/server";

import { apiBaseUrl } from "@/lib/api";
import { isIncidentId } from "@/lib/incidents";
import { sessionFetch } from "@/lib/session";

// Same-origin relay for the chat drawer: the browser holds only the UI's
// HttpOnly cookie, and the session is forwarded to the API server-side.

type Params = { params: Promise<{ id: string }> };

async function relay(id: string, init?: RequestInit): Promise<NextResponse> {
  if (!isIncidentId(id)) return NextResponse.json({ detail: "invalid incident id" }, { status: 400 });
  const authed = await sessionFetch();
  try {
    const response = await authed(`${apiBaseUrl()}/api/v1/incidents/${id}/chat`, { cache: "no-store", ...init });
    const body: unknown = await response.json().catch(() => ({}));
    return NextResponse.json(body, { status: response.status });
  } catch {
    return NextResponse.json({ detail: "API unavailable" }, { status: 502 });
  }
}

export async function GET(_request: Request, { params }: Params) {
  const { id } = await params;
  return relay(id);
}

export async function POST(request: Request, { params }: Params) {
  const { id } = await params;
  const body: unknown = await request.json().catch(() => null);
  const question =
    typeof body === "object" && body !== null && typeof (body as Record<string, unknown>).question === "string"
      ? String((body as Record<string, unknown>).question).slice(0, 2000)
      : "";
  if (!question.trim()) return NextResponse.json({ detail: "empty question" }, { status: 422 });
  return relay(id, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question }),
  });
}
