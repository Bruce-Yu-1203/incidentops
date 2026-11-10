import Link from "next/link";

import { needsSignIn } from "@/lib/auth";

/** Error line for a failed API read; a 401 becomes a sign-in prompt. */
export function LoadError({ what, error, next }: { what: string; error: string; next: string }) {
  if (needsSignIn(error)) {
    return (
      <p className="error">
        Sign in to view this {what}.{" "}
        <Link href={`/login?next=${encodeURIComponent(next)}`}>Sign in</Link>
      </p>
    );
  }
  return (
    <p className="error">
      Could not load {what}: {error}
    </p>
  );
}
