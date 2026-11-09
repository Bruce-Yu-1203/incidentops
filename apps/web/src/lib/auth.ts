// Session helpers shared by server components and server actions.
// The browser only holds an HttpOnly cookie for this UI; the API session token
// is forwarded server-side as an Authorization header. All authorization is
// decided by the API, never by the UI.

export const SESSION_COOKIE = "incidentops_session";

/** Wrap a fetch implementation so every request carries the session header. */
export function withSession(fetchImpl: typeof fetch, token: string | undefined): typeof fetch {
  if (!token) return fetchImpl;
  return ((input: RequestInfo | URL, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    headers.set("Authorization", `Session ${token}`);
    return fetchImpl(input, { ...init, headers });
  }) as typeof fetch;
}

export type CurrentUser = { username: string; role: string };

export function isCurrentUser(value: unknown): value is CurrentUser {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return typeof record.username === "string" && typeof record.role === "string";
}

export async function fetchMe(
  baseUrl: string,
  fetchImpl: typeof fetch = fetch,
): Promise<CurrentUser | null> {
  try {
    const response = await fetchImpl(`${baseUrl}/api/v1/auth/me`, { cache: "no-store" });
    if (!response.ok) return null;
    const body: unknown = await response.json();
    return isCurrentUser(body) ? body : null;
  } catch {
    return null;
  }
}

/** Only same-app relative paths are allowed as post-login redirects. */
export function safeNextPath(value: FormDataEntryValue | null): string {
  if (typeof value !== "string" || !value.startsWith("/") || value.startsWith("//")) return "/";
  return value;
}
