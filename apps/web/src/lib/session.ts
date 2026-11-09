import "server-only";

import { cookies } from "next/headers";

import { SESSION_COOKIE, withSession } from "./auth";

/** A fetch that forwards the caller's session to the API (server-side only). */
export async function sessionFetch(): Promise<typeof fetch> {
  const store = await cookies();
  return withSession(fetch, store.get(SESSION_COOKIE)?.value);
}
