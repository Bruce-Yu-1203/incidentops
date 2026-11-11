import { NextResponse } from "next/server";
import { apiBaseUrl } from "@/lib/api";
import { parseRange } from "@/lib/metrics";
import { sessionFetch } from "@/lib/session";

/** Same-origin read proxy; credentials and PromQL stay on the server. */
export async function GET(request: Request) {
  const range = parseRange(new URL(request.url).searchParams.get("minutes") ?? undefined);
  const authed = await sessionFetch();
  try {
    const response = await authed(`${apiBaseUrl()}/api/v1/metrics?minutes=${range}`, { cache: "no-store" });
    return NextResponse.json(await response.json(), { status: response.status });
  } catch {
    return NextResponse.json({ detail: "Metrics source unavailable" }, { status: 502 });
  }
}
