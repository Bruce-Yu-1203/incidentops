import Link from "next/link";
import { connection } from "next/server";

import { apiBaseUrl } from "@/lib/api";
import { fetchMe } from "@/lib/auth";
import { sessionFetch } from "@/lib/session";

import { logout } from "./session-actions";

export async function UserBar() {
  await connection();
  const user = await fetchMe(apiBaseUrl(), await sessionFetch());
  return (
    <nav className="userbar">
      <Link href="/">IncidentOps</Link>
      {user ? (
        <form action={logout}>
          <span>
            {user.username} ({user.role})
          </span>{" "}
          <button type="submit" className="link">
            Sign out
          </button>
        </form>
      ) : (
        <Link href="/login">Sign in</Link>
      )}
    </nav>
  );
}
