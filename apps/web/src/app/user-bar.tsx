import Link from "next/link";
import { connection } from "next/server";

import { AppNav } from "@/components/app-nav";
import { apiBaseUrl } from "@/lib/api";
import { fetchMe } from "@/lib/auth";
import { sessionFetch } from "@/lib/session";

import { logout } from "./session-actions";

export function HeaderShell({ children }: { children?: React.ReactNode }) {
  return (
    <header className="app-header">
      <span className="left">
        <Link href="/" className="brand">
          IncidentOps
        </Link>
        <AppNav />
      </span>
      {children}
    </header>
  );
}

export async function UserBar() {
  await connection();
  const user = await fetchMe(apiBaseUrl(), await sessionFetch());
  return (
    <HeaderShell>
      {user ? (
        <form action={logout} className="app-user">
          <span>
            {user.username} · {user.role}
          </span>
          <button type="submit" className="link">
            Sign out
          </button>
        </form>
      ) : (
        <Link href="/login" className="app-user">
          Sign in
        </Link>
      )}
    </HeaderShell>
  );
}
