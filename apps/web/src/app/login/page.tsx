import { Suspense } from "react";

import { login } from "../session-actions";

async function LoginForm({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; next?: string }>;
}) {
  const { error, next } = await searchParams;
  return (
    <>
      {error ? <p className="error">Invalid username or password.</p> : null}
      <form action={login} className="stack">
        <input type="hidden" name="next" value={next ?? "/"} />
        <label>
          Username
          <input name="username" autoComplete="username" required />
        </label>
        <label>
          Password
          <input name="password" type="password" autoComplete="current-password" required />
        </label>
        <button type="submit">Sign in</button>
      </form>
    </>
  );
}

export default function LoginPage(props: {
  searchParams: Promise<{ error?: string; next?: string }>;
}) {
  return (
    <main className="container narrow">
      <h1>Sign in</h1>
      <Suspense fallback={<p>Loading…</p>}>
        <LoginForm {...props} />
      </Suspense>
    </main>
  );
}
