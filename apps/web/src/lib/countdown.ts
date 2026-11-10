// Time left until an approval expires, formatted as a clock.

export type Remaining = { expired: boolean; seconds: number; label: string };

const pad = (n: number) => String(n).padStart(2, "0");

/** "08:42" under an hour, "1:02:03" above; "expired" once the TTL has passed. */
export function remaining(expiresAt: string, now: Date): Remaining {
  const end = new Date(expiresAt).getTime();
  if (Number.isNaN(end)) return { expired: true, seconds: 0, label: "unknown" };
  const seconds = Math.floor((end - now.getTime()) / 1000);
  if (seconds <= 0) return { expired: true, seconds: 0, label: "expired" };
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return {
    expired: false,
    seconds,
    label: h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`,
  };
}

/** Shortened action hash that keeps the algorithm and both ends visible. */
export function shortHash(hash: string): string {
  const [algo, digest] = hash.includes(":") ? hash.split(":", 2) : ["", hash];
  if (digest.length <= 12) return hash;
  return `${algo ? `${algo}:` : ""}${digest.slice(0, 6)}…${digest.slice(-4)}`;
}
