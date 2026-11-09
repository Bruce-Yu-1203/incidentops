"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { apiBaseUrl } from "@/lib/api";
import { SESSION_COOKIE, safeNextPath, withSession } from "@/lib/auth";

const SESSION_MAX_AGE_SECONDS = 12 * 3600;

export async function login(formData: FormData): Promise<void> {
  const username = formData.get("username");
  const password = formData.get("password");
  const next = safeNextPath(formData.get("next"));
  if (typeof username !== "string" || typeof password !== "string") {
    redirect("/login?error=1");
  }
  const response = await fetch(`${apiBaseUrl()}/api/v1/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username, password }),
    cache: "no-store",
  });
  const token = response.headers.get("X-Session-Token");
  if (!response.ok || !token) {
    redirect(`/login?error=1&next=${encodeURIComponent(next)}`);
  }
  const store = await cookies();
  store.set(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NODE_ENV === "production" && process.env.INCIDENTOPS_INSECURE_COOKIES !== "1",
    path: "/",
    maxAge: SESSION_MAX_AGE_SECONDS,
  });
  redirect(next);
}

export async function logout(): Promise<void> {
  const store = await cookies();
  const token = store.get(SESSION_COOKIE)?.value;
  if (token) {
    await withSession(fetch, token)(`${apiBaseUrl()}/api/v1/auth/logout`, { method: "POST" });
  }
  store.delete(SESSION_COOKIE);
  redirect("/login");
}
